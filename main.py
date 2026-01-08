from watcher import HuashengWatcher
import time
import os
import sys

def main():
    print("==========================================")
    print("   HUASHENG TOBACCO WATCHER (Pro)         ")
    print("   华盛烟草专用高定版监控机器人           ")
    print("==========================================")
    
    try:
        watcher = HuashengWatcher()
        watcher.run()
    except KeyboardInterrupt:
        print("\n🛑 程序已强制停止")
        os._exit(0)
    except Exception as e:
        print(f"\n❌ 致命错误: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()