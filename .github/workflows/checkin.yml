name: GLaDOS 2026 Checkin

on:
  schedule:
    # 北京时间 9:30 (UTC 1:30)
    - cron: '30 1 * * *'
    # 北京时间 21:30 (UTC 13:30)
    - cron: '30 13 * * *'
  workflow_dispatch:  # 允许手动触发
  push:
    branches: [main]  # 推送时也触发，保持活跃

jobs:
  checkin:
    runs-on: ubuntu-latest
    timeout-minutes: 10

    steps:
      - name: Checkout
        uses: actions/checkout@v4

      - name: Setup Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.11'
          cache: 'pip'

      - name: Install Dependencies
        run: pip install -r requirements.txt

      # 第一次尝试：失败不直接判死，交给下一步重试
      - name: Run Checkin
        id: checkin
        continue-on-error: true
        run: python checkin.py
        timeout-minutes: 5
        env:
          GLADOS_COOKIE: ${{ secrets.GLADOS_COOKIE }}
          PUSHPLUS_TOKEN: ${{ secrets.PUSHPLUS_TOKEN }}
          STEP_USER: ${{ secrets.STEP_USER }}
          STEP_PASSWORD: ${{ secrets.STEP_PASSWORD }}
          MAIL_USER: ${{ secrets.MAIL_USER }}
          MAIL_PASS: ${{ secrets.MAIL_PASS }}
          MAIL_TO: ${{ secrets.MAIL_TO }}

      - name: Retry on Failure
        id: retry
        if: steps.checkin.outcome == 'failure'
        continue-on-error: true
        run: |
          echo "First attempt failed, retrying in 60s..."
          sleep 60
          python checkin.py
        timeout-minutes: 5
        env:
          GLADOS_COOKIE: ${{ secrets.GLADOS_COOKIE }}
          PUSHPLUS_TOKEN: ${{ secrets.PUSHPLUS_TOKEN }}
          STEP_USER: ${{ secrets.STEP_USER }}
          STEP_PASSWORD: ${{ secrets.STEP_PASSWORD }}
          MAIL_USER: ${{ secrets.MAIL_USER }}
          MAIL_PASS: ${{ secrets.MAIL_PASS }}
          MAIL_TO: ${{ secrets.MAIL_TO }}

      # 只有两次都失败才把整个 job 判红 —— 这样 GitHub 的失败邮件只在真失败时发一次
      - name: Fail if both attempts unsuccessful
        if: steps.checkin.outcome == 'failure' && steps.retry.outcome == 'failure'
        run: |
          echo "❌ 两次签到尝试均未成功，请检查 GLADOS_COOKIE 是否过期"
          exit 1
