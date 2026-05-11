#!/bin/bash

# 一键清除所有分析记录脚本

cd "$(dirname "$0")/.." || exit 1
APP_DIR=$(pwd)
DB_FILE="$APP_DIR/data/app.db"

python3 << EOF
import sqlite3
import sys

db_path = "$DB_FILE"

try:
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    # 检查卡住的记录
    cursor.execute("SELECT id, algorithm, status FROM analyses WHERE status = 'analyzing'")
    stuck = cursor.fetchall()
    if stuck:
        print(f"发现 {len(stuck)} 条卡住的分析:")
        for s in stuck:
            print(f"  - {s[0]} ({s[1]})")
    
    # 删除所有 segments
    cursor.execute('DELETE FROM segments')
    segments_del = cursor.rowcount
    
    # 删除所有 analyses
    cursor.execute('DELETE FROM analyses')
    analyses_del = cursor.rowcount
    
    # 重置 videos 状态
    cursor.execute('UPDATE videos SET analysis_id = NULL, status = "pending", progress_percent = 0, progress_message = NULL')
    
    conn.commit()
    print(f"\n已清除: {analyses_del} 条分析记录, {segments_del} 条片段")
    print("视频状态已重置为 pending")
    
    conn.close()
    
except Exception as e:
    print(f"错误: {e}")
    sys.exit(1)
EOF
