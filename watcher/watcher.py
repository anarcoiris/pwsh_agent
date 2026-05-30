#!/usr/bin/env python3

import os
import time
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

class ChangeHandler(FileSystemEventHandler):
    def on_modified(self, event):
        print(f'File {event.src_path} has been modified')

if __name__ == '__main__':
    path_to_watch = '.'  # Current directory
    event_handler = ChangeHandler()
    observer = Observer()
    observer.schedule(event_handler, path=path_to_watch, recursive=True)
    observer.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
    observer.join()