from datetime import datetime

def fmt_time(ts):
    if ts:
        try:
            return datetime.fromtimestamp(ts).strftime('%Y-%m-%d %H:%M:%S')
        except Exception:
            return str(ts)
    return '未知时间'