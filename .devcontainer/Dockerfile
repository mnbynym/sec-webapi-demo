FROM mcr.microsoft.com/devcontainers/python:1-3.12-bookworm

# 署名鍵を検証できない Yarn の apt リポジトリを外す（Feature の apt-get update 失敗対策）
RUN rm -f /etc/apt/sources.list.d/yarn.list