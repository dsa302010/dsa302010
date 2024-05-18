##试着将优秀的forks自动同步过来
诉求：
在这个项目中，我的仓库地址是：https://github.com/jixiexiaoge/openpilot，我想分别复制这些分支 https://github.com/FrogAi/FrogPilot 的brunch有FrogPilot-Old和FrogPilot，第二个分支是 https://github.com/sunnypilot/sunnypilot 的 brunch是 dev-c3，以及第三个分支 https://github.com/dragonpilot-community/dragonpilot 的brunch是d3
效果：
通过以上步骤，你已经在 GitHub 仓库中设置了一个 GitHub Actions 工作流，该工作流会每天自动同步以下分支到你的仓库中：

FrogPilot-Old 和 FrogPilot 从 https://github.com/FrogAi/FrogPilot
dev-c3 从 https://github.com/sunnypilot/sunnypilot
d3 从 https://github.com/dragonpilot-community/dragonpilot
这个 GitHub Actions 工作流将每天自动运行，并确保你的仓库始终与这些远程分支保持同步。

需要创建一个 GitHub 个人访问令牌 (Personal Access Token)，并将其配置为 GitHub Actions 的秘密变量 (secret)，然后在工作流中使用它来进行身份验证。

以下是具体步骤：

步骤一：创建 GitHub 个人访问令牌
登录到 GitHub。
点击你的头像，然后点击“Settings”。
在左侧栏中，点击“Developer settings”。
点击“Personal access tokens”。
点击“Generate new token”。
给令牌起一个名字，如 GitHub Actions Token。
选择适当的权限，至少选择 repo 和 workflow。
点击“Generate token”并保存生成的令牌。
步骤二：将令牌添加到 GitHub 仓库的秘密变量中
进入你的仓库 https://github.com/jixiexiaoge/openpilot。
点击“Settings”。
在左侧栏中，点击“Secrets and variables” > “Actions”。
点击“New repository secret”。
给秘密变量起一个名字，如 ACTIONS_PAT。
在“Value”字段中粘贴刚刚生成的个人访问令牌。
点击“Add secret”。
步骤三：修改 GitHub Actions 工作流文件以使用个人访问令牌
编辑你的 sync.yml 文件，使用个人访问令牌进行身份验证。修改后的文件如下：

yaml
复制代码
name: Sync Forks

on:
  schedule:
    - cron: '0 0 * * *'  # 每天同步一次
  push:
    branches:
      - main

jobs:
  sync:
    runs-on: ubuntu-latest
    steps:
    - name: Checkout repository
      uses: actions/checkout@v2
      with:
        token: ${{ secrets.ACTIONS_PAT }}

    - name: Set up Git
      run: |
        git config --global user.name 'github-actions[bot]'
        git config --global user.email '41898282+github-actions[bot]@users.noreply.github.com'

    - name: Add remote repositories
      run: |
        git remote add frogpilot https://github.com/FrogAi/FrogPilot
        git remote add sunnypilot https://github.com/sunnypilot/sunnypilot
        git remote add dragonpilot https://github.com/dragonpilot-community/dragonpilot

    - name: Fetch and merge FrogPilot-Old
      run: |
        git fetch frogpilot
        git checkout -B FrogPilot-Old frogpilot/FrogPilot-Old
        git push origin FrogPilot-Old --force

    - name: Fetch and merge FrogPilot
      run: |
        git fetch frogpilot
        git checkout -B FrogPilot frogpilot/FrogPilot
        git push origin FrogPilot --force

    - name: Fetch and merge dev-c3
      run: |
        git fetch sunnypilot
        git checkout -B dev-c3 sunnypilot/dev-c3
        git push origin dev-c3 --force

    - name: Fetch and merge d3
      run: |
        git fetch dragonpilot
        git checkout -B d3 dragonpilot/d3
        git push origin d3 --force
步骤四：提交并推送更改
保存并提交 sync.yml 文件，然后推送到 GitHub：

bash
复制代码
git add .github/workflows/sync.yml
git commit -m "Update sync workflow with personal access token"
git push origin main
解释
在上面的工作流中，我们在 actions/checkout@v2 步骤中使用了 token: ${{ secrets.ACTIONS_PAT }}，这是使用你在 GitHub Secrets 中配置的个人访问令牌进行身份验证。这将允许 GitHub Actions bot 有权限推送更改到你的仓库。

完成上述步骤后，GitHub Actions 工作流应该能够成功运行，并且能够将远程分支同步到你的仓库中。
