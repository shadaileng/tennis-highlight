#!/bin/bash

# Tennis Highlight 服务管理脚本

cd "$(dirname "$0")/.." || exit 1
APP_DIR=$(pwd)

PID_FILE="$APP_DIR/logs/uvicorn.pid"
LOG_FILE="$APP_DIR/logs/uvicorn.log"

stop_service() {
    if [ -f "$PID_FILE" ]; then
        PID=$(cat "$PID_FILE")
        if kill -0 "$PID" 2>/dev/null; then
            echo "Stopping service (PID: $PID)..."
            kill "$PID"
            sleep 2
            if kill -0 "$PID" 2>/dev/null; then
                kill -9 "$PID" 2>/dev/null
                sleep 1
            fi
        fi
        rm -f "$PID_FILE"
    fi
    # Kill by process name
    pkill -f "uvicorn app.main:app" 2>/dev/null
    # Kill by port (most reliable — catches any lingering process on 8000)
    fuser -k 8000/tcp 2>/dev/null
    sleep 1
    echo "Service stopped."
}

start_service() {
    # 确保端口未被占用
    if fuser 8000/tcp 2>/dev/null; then
        echo "Port 8000 still in use. Waiting..."
        sleep 2
        fuser -k 8000/tcp 2>/dev/null
        sleep 1
    fi

    echo "Starting service..."
    nohup python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 > "$LOG_FILE" 2>&1 &
    PID=$!
    echo "$PID" > "$PID_FILE"

    # Wait for startup (up to 10 seconds)
    for i in $(seq 1 10); do
        sleep 1
        if ! kill -0 "$PID" 2>/dev/null; then
            echo "Failed to start service. Check $LOG_FILE"
            tail -20 "$LOG_FILE"
            exit 1
        fi
        if grep -q "Uvicorn running on" "$LOG_FILE" 2>/dev/null; then
            echo "Service started (PID: $PID)"
            tail -5 "$LOG_FILE"
            return 0
        fi
    done

    # Timeout
    echo "Failed to start service (timeout). Check $LOG_FILE"
    tail -20 "$LOG_FILE"
    exit 1
}

restart_service() {
    stop_service
    start_service
}

status_service() {
    if [ -f "$PID_FILE" ]; then
        PID=$(cat "$PID_FILE")
        if kill -0 "$PID" 2>/dev/null; then
            echo "Service running (PID: $PID)"
            return 0
        fi
    fi
    # Check by port
    if pgrep -f "uvicorn app.main:app" > /dev/null; then
        echo "Service running (found by process)"
        return 0
    fi
    echo "Service not running"
    return 1
}

case "$1" in
    start)
        start_service
        ;;
    stop)
        stop_service
        ;;
    restart)
        restart_service
        ;;
    status)
        status_service
        ;;
    logs)
        tail -50 "$LOG_FILE"
        ;;
    *)
        echo "Usage: $0 {start|stop|restart|status|logs}"
        exit 1
        ;;
esac
