#!/bin/bash

# 定义要执行的 Git 命令
GIT_COMMAND="git push -u origin main"

# 定义等待时间（秒）
DELAY=1

echo "========================================"
echo "开始循环推送 Git 提交。如果失败，将等待 ${DELAY} 秒后重试..."
echo "========================================"

# 无限循环，直到命令成功
while true
do
    echo "--- $(date) ---"
    echo "正在执行: ${GIT_COMMAND}"
    
    # 执行 Git 命令
    ${GIT_COMMAND}
    
    # 检查上一个命令的退出代码
    if [ $? -eq 0 ]; then
        echo "✅ Git 推送成功！循环终止。"
        break # 成功则跳出循环
    else
        echo "❌ Git 推送失败 (退出代码 $?)。等待 ${DELAY} 秒后重试..."
        sleep ${DELAY} # 等待指定时间
    fi
done
