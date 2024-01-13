#!/usr/bin/env python3
from cereal import car, custom
from math import fabs, exp
from panda import Panda

from openpilot.common.conversions import Conversions as CV
from openpilot.common.params import Params
from openpilot.selfdrive.car import create_button_events, get_safety_config
from openpilot.selfdrive.car.gm.radar_interface import RADAR_HEADER_MSG
from openpilot.selfdrive.car.gm.values import CAR, CruiseButtons, CarControllerParams, EV_CAR, CAMERA_ACC_CAR, CanBus, GMFlags, CC_ONLY_CAR, SDGM_CAR, SLOW_ACC
from openpilot.selfdrive.car.interfaces import CarInterfaceBase, TorqueFromLateralAccelCallbackType, FRICTION_THRESHOLD
from openpilot.selfdrive.controls.lib.drive_helpers import get_friction

ButtonType = car.CarState.ButtonEvent.Type
EventName = car.CarEvent.EventName
FrogPilotEventName = custom.FrogPilotEvents
GearShifter = car.CarState.GearShifter
TransmissionType = car.CarParams.TransmissionType
NetworkLocation = car.CarParams.NetworkLocation
BUTTONS_DICT = {CruiseButtons.RES_ACCEL: ButtonType.accelCruise, CruiseButtons.DECEL_SET: ButtonType.decelCruise,
                CruiseButtons.MAIN: ButtonType.altButton3, CruiseButtons.CANCEL: ButtonType.cancel}

ACCELERATOR_POS_MSG = 0xbe
BSM_MSG = 0x142

NON_LINEAR_TORQUE_PARAMS = {
  CAR.BOLT_EUV: [2.6531724862969748, 1.0, 0.1919764879840985, 0.009054123646805178],
  CAR.BOLT_CC: [2.6531724862969748, 1.0, 0.1919764879840985, 0.009054123646805178],
  CAR.ACADIA: [4.78003305, 1.0, 0.3122, 0.05591772],
  CAR.SILVERADO: [3.29974374, 1.0, 0.25571356, 0.0465122]
}


class CarInterface(CarInterfaceBase):
  @staticmethod
  def get_pid_accel_limits(CP, current_speed, cruise_speed, sport_plus):
    if sport_plus:
      return CarControllerParams.ACCEL_MIN, CarControllerParams.ACCEL_MAX_PLUS
    else:
      return CarControllerParams.ACCEL_MIN, CarControllerParams.ACCEL_MAX

  # Determined by iteratively plotting and minimizing error for f(angle, speed) = steer.
  @staticmethod
  def get_steer_feedforward_volt(desired_angle, v_ego):
    desired_angle *= 0.02904609
    sigmoid = desired_angle / (1 + fabs(desired_angle))
    return 0.10006696 * sigmoid * (v_ego + 3.12485927)

  def get_steer_feedforward_function(self):
    if self.CP.carFingerprint in (CAR.VOLT, CAR.VOLT_CC):
      return self.get_steer_feedforward_volt
    else:
      return CarInterfaceBase.get_steer_feedforward_default

  def torque_from_lateral_accel_siglin(self, lateral_accel_value: float, torque_params: car.CarParams.LateralTorqueTuning,
                                       lateral_accel_error: float, lateral_accel_deadzone: float, friction_compensation: bool) -> float:
    friction = get_friction(lateral_accel_error, lateral_accel_deadzone, FRICTION_THRESHOLD, torque_params, friction_compensation)

    def sig(val):
      return 1 / (1 + exp(-val)) - 0.5

    # The "lat_accel vs torque" relationship is assumed to be the sum of "sigmoid + linear" curves
    # An important thing to consider is that the slope at 0 should be > 0 (ideally >1)
    # This has big effect on the stability about 0 (noise when going straight)
    # ToDo: To generalize to other GMs, explore tanh function as the nonlinear
    non_linear_torque_params = NON_LINEAR_TORQUE_PARAMS.get(self.CP.carFingerprint)
    assert non_linear_torque_params, "The params are not defined"
    a, b, c, _ = non_linear_torque_params
    steer_torque = (sig(lateral_accel_value * a) * b) + (lateral_accel_value * c)
    return float(steer_torque) + friction

  def torque_from_lateral_accel(self) -> TorqueFromLateralAccelCallbackType:
    if self.CP.carFingerprint in NON_LINEAR_TORQUE_PARAMS:
      return self.torque_from_lateral_accel_siglin
    else:
      return self.torque_from_lateral_accel_linear

  @staticmethod
  def _get_params(ret, candidate, fingerprint, car_fw, experimental_long, docs):
    # FrogPilot variables
    params = params()
    useGasRegenCmd = params.get_bool("GasRegenCmd")

    ret.carName = "gm"
    ret.safetyConfigs = [get_safety_config(car.CarParams.SafetyModel.gm)]
    ret.autoResumeSng = False

    if candidate in EV_CAR:
      ret.transmissionType = TransmissionType.direct
    else:
      ret.transmissionType = TransmissionType.automatic

    ret.longitudinalTuning.deadzoneBP = [0.]
    ret.longitudinalTuning.deadzoneV = [0.15]

    ret.longitudinalTuning.kpBP = [5., 35.]
    ret.longitudinalTuning.kiBP = [0.]

    if candidate in CAMERA_ACC_CAR:
      ret.experimentalLongitudinalAvailable = candidate not in CC_ONLY_CAR
      ret.networkLocation = NetworkLocation.fwdCamera
      ret.radarUnavailable = True  # no radar
      ret.pcmCruise = True
      ret.safetyConfigs[0].safetyParam |= Panda.FLAG_GM_HW_CAM
      ret.minEnableSpeed = 5 * CV.KPH_TO_MS
      ret.minSteerSpeed = 10 * CV.KPH_TO_MS

      # Tuning for experimental long
      ret.longitudinalTuning.kpV = [2.0, 1.5]
      ret.longitudinalTuning.kiV = [0.72]
      ret.stoppingDecelRate = 2.0  # reach brake quickly after enabling
      ret.vEgoStopping = 0.25
      ret.vEgoStarting = 0.25

      if candidate in SLOW_ACC and useGasRegenCmd:
        ret.longitudinalTuning.kpV = [1.5, 1.125]

      if experimental_long:
        ret.pcmCruise = False
        ret.openpilotLongitudinalControl = True
        ret.safetyConfigs[0].safetyParam |= Panda.FLAG_GM_HW_CAM_LONG

    elif candidate in SDGM_CAR:
      ret.experimentalLongitudinalAvailable = False
      ret.networkLocation = NetworkLocation.fwdCamera
      ret.pcmCruise = True
      ret.radarUnavailable = True
      ret.minEnableSpeed = -1.  # engage speed is decided by ASCM
      ret.minSteerSpeed = 30 * CV.MPH_TO_MS
      ret.safetyConfigs[0].safetyParam |= Panda.FLAG_GM_HW_SDGM

    else:  # ASCM, OBD-II harness
      ret.openpilotLongitudinalControl = True
      ret.networkLocation = NetworkLocation.gateway
      ret.radarUnavailable = RADAR_HEADER_MSG not in fingerprint[CanBus.OBSTACLE] and not docs
      ret.pcmCruise = False  # stock non-adaptive cruise control is kept off
      # supports stop and go, but initial engage must (conservatively) be above 18mph
      ret.minEnableSpeed = 18 * CV.MPH_TO_MS
      ret.minSteerSpeed = 7 * CV.MPH_TO_MS

      # Tuning
      ret.longitudinalTuning.kpV = [2.4, 1.5]
      ret.longitudinalTuning.kiV = [0.36]

    # These cars have been put into dashcam only due to both a lack of users and test coverage.
    # These cars likely still work fine. Once a user confirms each car works and a test route is
    # added to selfdrive/car/tests/routes.py, we can remove it from this list.
    ret.dashcamOnly = candidate in {CAR.CADILLAC_ATS, CAR.HOLDEN_ASTRA, CAR.MALIBU, CAR.BUICK_REGAL, CAR.EQUINOX} or \
                      (ret.networkLocation == NetworkLocation.gateway and ret.radarUnavailable)

    # Start with a baseline tuning for all GM vehicles. Override tuning as needed in each model section below.
    ret.lateralTuning.pid.kiBP, ret.lateralTuning.pid.kpBP = [[0.], [0.]]
    ret.lateralTuning.pid.kpV, ret.lateralTuning.pid.kiV = [[0.2], [0.00]]
    ret.lateralTuning.pid.kf = 0.00004   # full torque for 20 deg at 80mph means 0.00007818594
    ret.steerActuatorDelay = 0.1  # Default delay, not measured yet
    ret.tireStiffnessFactor = 0.444  # not optimized yet

    ret.steerLimitTimer = 0.4
    ret.radarTimeStep = 0.0667  # GM radar runs at 15Hz instead of standard 20Hz
    ret.longitudinalActuatorDelayUpperBound = 0.5  # large delay to initially start braking

    if candidate in (CAR.VOLT, CAR.VOLT_CC):
      ret.mass = 1607.
      ret.wheelbase = 2.69
      ret.steerRatio = 17.7  # Stock 15.7, LiveParameters
      ret.tireStiffnessFactor = 0.469  # Stock Michelin Energy Saver A/S, LiveParameters
      ret.centerToFront = ret.wheelbase * 0.45  # Volt Gen 1, TODO corner weigh

      ret.lateralTuning.pid.kpBP = [0., 40.]
      ret.lateralTuning.pid.kpV = [0., 0.17]
      ret.lateralTuning.pid.kiBP = [0.]
      ret.lateralTuning.pid.kiV = [0.]
      ret.lateralTuning.pid.kf = 1.  # get_steer_feedforward_volt()
      ret.steerActuatorDelay = 0.2

    elif candidate == CAR.MALIBU:
      ret.mass = 1496.
      ret.wheelbase = 2.83
      ret.steerRatio = 15.8
      ret.centerToFront = ret.wheelbase * 0.4  # wild guess

    elif candidate == CAR.HOLDEN_ASTRA:
      ret.mass = 1363.
      ret.wheelbase = 2.662
      # Remaining parameters copied from Volt for now
      ret.centerToFront = ret.wheelbase * 0.4
      ret.steerRatio = 15.7

    elif candidate == CAR.ACADIA:
      ret.minEnableSpeed = -1.  # engage speed is decided by pcm
      ret.mass = 4353. * CV.LB_TO_KG
      ret.wheelbase = 2.86
      ret.steerRatio = 14.4  # end to end is 13.46
      ret.centerToFront = ret.wheelbase * 0.4
      ret.steerActuatorDelay = 0.2
      CarInterfaceBase.configure_torque_tune(candidate, ret.lateralTuning)

    elif candidate == CAR.BUICK_LACROSSE:
      ret.mass = 1712.
      ret.wheelbase = 2.91
      ret.steerRatio = 15.8
      ret.centerToFront = ret.wheelbase * 0.4  # wild guess
      CarInterfaceBase.configure_torque_tune(candidate, ret.lateralTuning)

    elif candidate == CAR.BUICK_REGAL:
      ret.mass = 3779. * CV.LB_TO_KG  # (3849+3708)/2
      ret.wheelbase = 2.83  # 111.4 inches in meters
      ret.steerRatio = 14.4  # guess for tourx
      ret.centerToFront = ret.wheelbase * 0.4  # guess for tourx

    elif candidate == CAR.CADILLAC_ATS:
      ret.mass = 1601.
      ret.wheelbase = 2.78
      ret.steerRatio = 15.3
      ret.centerToFront = ret.wheelbase * 0.5

    elif candidate == CAR.ESCALADE:
      ret.minEnableSpeed = -1.  # engage speed is decided by pcm
      ret.mass = 5653. * CV.LB_TO_KG  # (5552+5815)/2
      ret.wheelbase = 2.95  # 116 inches in meters
      ret.steerRatio = 17.3
      ret.centerToFront = ret.wheelbase * 0.5
      CarInterfaceBase.configure_torque_tune(candidate, ret.lateralTuning)

    elif candidate in (CAR.ESCALADE_ESV, CAR.ESCALADE_ESV_2019):
      ret.minEnableSpeed = -1.  # engage speed is decided by pcm
      ret.mass = 2739.
      ret.wheelbase = 3.302
      ret.steerRatio = 17.3
      ret.centerToFront = ret.wheelbase * 0.5
      ret.tireStiffnessFactor = 1.0

      if candidate == CAR.ESCALADE_ESV:
        ret.lateralTuning.pid.kiBP, ret.lateralTuning.pid.kpBP = [[10., 41.0], [10., 41.0]]
        ret.lateralTuning.pid.kpV, ret.lateralTuning.pid.kiV = [[0.13, 0.24], [0.01, 0.02]]
        ret.lateralTuning.pid.kf = 0.000045
      else:
        ret.steerActuatorDelay = 0.2
        CarInterfaceBase.configure_torque_tune(candidate, ret.lateralTuning)

    elif candidate in (CAR.BOLT_EUV, CAR.BOLT_CC):
      ret.mass = 1669.
      ret.wheelbase = 2.63779
      ret.steerRatio = 16.8
      ret.centerToFront = ret.wheelbase * 0.4
      ret.tireStiffnessFactor = 1.0
      ret.steerActuatorDelay = 0.2
      CarInterfaceBase.configure_torque_tune(candidate, ret.lateralTuning)

    elif candidate == CAR.SILVERADO:
      ret.mass = 2450.
      ret.wheelbase = 3.75
      ret.steerRatio = 16.3
      ret.centerToFront = ret.wheelbase * 0.5
      ret.tireStiffnessFactor = 1.0
      if useGasRegenCmd:
        ret.stopAccel = -0.25
      # On the Bolt, the ECM and camera independently check that you are either above 5 kph or at a stop
      # with foot on brake to allow engagement, but this platform only has that check in the camera.
      # TODO: check if this is split by EV/ICE with more platforms in the future
      if ret.openpilotLongitudinalControl:
        ret.minEnableSpeed = -1.
      CarInterfaceBase.configure_torque_tune(candidate, ret.lateralTuning)

    elif candidate in (CAR.EQUINOX, CAR.EQUINOX_CC):
      ret.mass = 3500. * CV.LB_TO_KG
      ret.wheelbase = 2.72
      ret.steerRatio = 14.4
      ret.centerToFront = ret.wheelbase * 0.4
      CarInterfaceBase.configure_torque_tune(candidate, ret.lateralTuning)

    elif candidate in (CAR.TRAILBLAZER, CAR.TRAILBLAZER_CC):
      ret.mass = 1345.
      ret.wheelbase = 2.64
      ret.steerRatio = 16.8
      ret.centerToFront = ret.wheelbase * 0.4
      ret.tireStiffnessFactor = 1.0
      ret.steerActuatorDelay = 0.2
      CarInterfaceBase.configure_torque_tune(candidate, ret.lateralTuning)

    elif candidate in (CAR.SUBURBAN, CAR.SUBURBAN_CC):
      ret.mass = 2731.
      ret.wheelbase = 3.302
      ret.steerRatio = 17.3 # COPIED FROM SILVERADO
      ret.centerToFront = ret.wheelbase * 0.49
      ret.steerActuatorDelay = 0.075
      CarInterfaceBase.configure_torque_tune(candidate, ret.lateralTuning)

    elif candidate == CAR.YUKON_CC:
      ret.minSteerSpeed = -1 * CV.MPH_TO_MS
      ret.mass = 5602. * CV.LB_TO_KG  # (3849+3708)/2
      ret.wheelbase = 2.95  # 116 inches in meters
      ret.steerRatio = 16.3  # guess for tourx
      ret.steerRatioRear = 0.  # unknown online
      ret.centerToFront = 2.59  # ret.wheelbase * 0.4 # wild guess
      ret.steerActuatorDelay = 0.2
      CarInterfaceBase.configure_torque_tune(candidate, ret.lateralTuning)

    elif candidate == CAR.XT4:
      ret.mass = 3660. * CV.LB_TO_KG
      ret.wheelbase = 2.78
      ret.steerRatio = 14.4
      ret.centerToFront = ret.wheelbase * 0.4
      ret.steerActuatorDelay = 0.2
      CarInterfaceBase.configure_torque_tune(candidate, ret.lateralTuning)

    elif candidate == CAR.CT6_CC:
      ret.wheelbase = 3.11
      ret.mass = 5198. * CV.LB_TO_KG
      ret.centerToFront = ret.wheelbase * 0.4
      ret.steerRatio = 17.7
      CarInterfaceBase.configure_torque_tune(candidate, ret.lateralTuning)

    elif candidate in CC_ONLY_CAR:
      ret.flags |= GMFlags.CC_LONG.value
      ret.safetyConfigs[0].safetyParam |= Panda.FLAG_GM_CC_LONG
      ret.radarUnavailable = True
      ret.experimentalLongitudinalAvailable = False
      ret.minEnableSpeed = 24 * CV.MPH_TO_MS
      ret.openpilotLongitudinalControl = True
      ret.pcmCruise = False

      ret.longitudinalTuning.deadzoneBP = [0.]
      ret.longitudinalTuning.deadzoneV = [0.56]  # == 2 km/h/s, 1.25 mph/s
      ret.stoppingDecelRate = 11.18  # == 25 mph/s (.04 rate)
      ret.longitudinalActuatorDelayLowerBound = 1.  # TODO: measure this
      ret.longitudinalActuatorDelayUpperBound = 2.

      ret.longitudinalTuning.kpBP = [10.7, 10.8, 28.]  # 10.7 m/s == 24 mph
      ret.longitudinalTuning.kpV = [0., 20., 20.]  # set lower end to 0 since we can't drive below that speed
      ret.longitudinalTuning.kiBP = [0.]
      ret.longitudinalTuning.kiV = [0.1]

    if candidate in CC_ONLY_CAR:
      ret.safetyConfigs[0].safetyParam |= Panda.FLAG_GM_NO_ACC

    if ACCELERATOR_POS_MSG not in fingerprint[CanBus.POWERTRAIN]:
      ret.flags |= GMFlags.NO_ACCELERATOR_POS_MSG.value

    # Detect if BSM message is present
    ret.enableBsm = BSM_MSG in fingerprint[CanBus.POWERTRAIN]

    return ret

  # returns a car.CarState
  def _update(self, c):
    ret = self.CS.update(self.cp, self.cp_cam, self.cp_loopback)

    # Don't add event if transitioning from INIT, unless it's to an actual button
    if self.CS.cruise_buttons != CruiseButtons.UNPRESS or self.CS.prev_cruise_buttons != CruiseButtons.INIT:
      ret.buttonEvents = create_button_events(self.CS.cruise_buttons, self.CS.prev_cruise_buttons, BUTTONS_DICT,
                                              unpressed_btn=CruiseButtons.UNPRESS)

    # The ECM allows enabling on falling edge of set, but only rising edge of resume
    events = self.create_common_events(ret, extra_gears=[GearShifter.sport, GearShifter.low,
                                                         GearShifter.eco, GearShifter.manumatic],
                                       pcm_enable=self.CP.pcmCruise, enable_buttons=(ButtonType.decelCruise,))
    if not self.CP.pcmCruise:
      if any(b.type == ButtonType.accelCruise and b.pressed for b in ret.buttonEvents):
        events.add(EventName.buttonEnable)

    # Enabling at a standstill with brake is allowed
    # TODO: verify 17 Volt can enable for the first time at a stop and allow for all GMs
    below_min_enable_speed = ret.vEgo < self.CP.minEnableSpeed or self.CS.moving_backward
    if below_min_enable_speed and not (ret.standstill and ret.brake >= 20 and
                                       (self.CP.networkLocation == NetworkLocation.fwdCamera and not self.CP.carFingerprint in SDGM_CAR)):
      events.add(EventName.belowEngageSpeed)
    if ret.cruiseState.standstill:
      events.add(EventName.resumeRequired)
    if ret.vEgo < self.CP.minSteerSpeed:
      events.add(EventName.belowSteerSpeed)

    if (self.CP.flags & GMFlags.CC_LONG.value) and ret.vEgo < self.CP.minEnableSpeed and ret.cruiseState.enabled:
      events.add(EventName.speedTooLow)

    ret.events = events.to_msg()

    return ret

  def apply(self, c, now_nanos, sport_plus):
    return self.CC.update(c, self.CS, now_nanos)