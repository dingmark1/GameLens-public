import sys

from PyQt6.QtWidgets import QApplication

from ui.main_window import MainWindow
from ui.screen_region_selector import reset_selection_config


def main() -> int:
    # QApplication 是 Qt GUI 程序的核心对象：
    # - 负责管理应用级资源和事件分发
    # - sys.argv 用于接收并传递命令行参数（如平台参数等）
    app = QApplication(sys.argv)

    # 每次启动时重置框选区域，确保后续识别不会误用上次缓存坐标。
    reset_selection_config()

    # 创建主窗口实例，窗口的标题、尺寸、居中逻辑在 MainWindow 内完成。
    window = MainWindow()

    # 显示主窗口。只有调用 show() 后窗口才会真正出现在桌面上。
    window.show()

    # 进入 Qt 事件循环：
    # - 处理鼠标、键盘、重绘、关闭等事件
    # - 当窗口关闭后，事件循环退出并返回退出码
    return app.exec()


if __name__ == "__main__":
    # 以脚本方式直接运行时，执行 main 并把返回值作为进程退出码抛给系统。
    raise SystemExit(main())
