import sys
from time import sleep
import Queue
import multiprocessing
from collections import namedtuple
from datetime import datetime, timedelta
import os

from path import path, pickle

from video import cv, CVCaptureProperties
from silence import Silence


class CVCaptureConfig(object):
    type_names = ('camera', 'file')
    types = namedtuple('CVCaptureTypes', type_names)(**dict([(n, i) for i, n in enumerate(type_names)]))

    def __init__(self, source, type_=None):
        self.source = source
        if type_ is None:
            type_ = self.types.camera
        elif type_ not in self.types:
            type_ = type_.strip()
            if not type_ in self.types._fields:
                raise ValueError, 'Invalid type: %s' % type_
            else:
                type_ = getattr(self.types, type_)
        self.type_ = type_

    def create_capture(self):
        if self.type_ == self.types.camera:
            cap = cv.CaptureFromCAM(self.source)
        elif self.type_ == self.types.file:
            source_path = path(self.source).abspath()
            if not source_path.exists():
                raise IOError, 'Capture source path is not accessible: %s' % source_path.abspath()
            cap = cv.CaptureFromFile(self.source)
        else:
            raise ValueError, 'Unsupported capture type: %s' % self.type_
        return cap

    def test_capture(self):
        cap = self.create_capture()
        result = cv.GrabFrame(cap)
        del cap
        return (result == 0)


class RecorderLog(object):
    def __init__(self, fps):
        self.fps = fps
        self.times = [datetime.now()]
        self.sleep_times = []
        self.record_times = []
        self.frame_lengths = []

    def print_summary(self):
        from pprint import pprint

        print 'captured %d frames' % len(self.times)
        print '  first frame: %s' % self.times[0]
        print '  last frame:  %s' % self.times[-1]
        print '  recording length: %s' % (self.times[-1] - self.times[0]).total_seconds()

        print '  Frame rate info:'
        print '    mean: %s' % (1.0 / self.frame_lengths.mean())
        print '    max:  %s' % (1.0 / self.frame_lengths.min())
        print '    min:  %s' % (1.0 / self.frame_lengths.max())

        pprint(self.frame_lengths)

    def save(self, out_file):
        out_path = path(out_file)
        out_path.pickle_dump([self.fps, self.frame_lengths, self.times, self.sleep_times, self.record_times], protocol=pickle.HIGHEST_PROTOCOL)

    def finish(self):
        import numpy as np

        del self.times[0]

        self.frame_lengths = np.array([(self.times[i + 1] - self.times[i]).total_seconds()  for i in range(len(self.times) - 1)])


class RecorderChild(object):
    STATES = dict(RECORDING=10, STOPPED=20)

    def __init__(self, conn, output_path, cam_cap, fps=24):
        self.conn = conn
        self.output_path = path(output_path)
        self.fps = fps
        self.cam_cap = cam_cap
        self.cam_cap.init_capture()
        self.writer = self._get_writer()
        self.state = self.STATES['STOPPED']
        self.frame_period = 1.0 / self.fps
        
    def _get_writer(self):
        if os.name == 'nt':
            codec = 'I420'
            #codec = 'FFV1'
            #codec = 'XVID'
            #codec = 'MJPG'
            #codec = 'PIM1'
            #codec = 'DIVX'
            writer = cv.CreateVideoWriter(self.output_path, cv.CV_FOURCC(*codec),
                self.fps, self.cam_cap.dimensions, True)
        else:
            codec = 'XVID'

            import numpy as np

            #dimensions = np.array(np.array(self.cam_cap.dimensions) * 0.25, dtype=int)
            dimensions = np.array(np.array(self.cam_cap.dimensions), dtype=int)
            writer = cv.CreateVideoWriter(self.output_path, cv.CV_FOURCC(*codec),
                                            self.fps, tuple(dimensions), True)
        return writer

    def main(self):
        import numpy as np

        #with Silence():
        prev_frame = None

        self.cam_cap.get_framerate_info()
        frame_count = 0
        record_id = 0
        avg_count = 1
        record_times_smooth = np.array(avg_count * [0.1 * self.frame_period])
        frame_periods = np.array(avg_count * [0.95 * self.frame_period])
        print 'Target FPS: %.4f' % (self.fps)

        log = RecorderLog(self.fps)

        iter_count = 1000
        extra_start = datetime.now()
        for i in range(iter_count):
            log.sleep_times.append(1)
            log.record_times.append(1)
            log.times.append(1)
            frame_count += 1
            record_id = (record_id + 1) % avg_count
        log.sleep_times = []
        log.record_times = []
        del log.times[1:]
        record_id = 0
        frame_count = 0
        extra_time = (datetime.now() - extra_start).total_seconds() / float(iter_count)

        self.conn.send('ready')

        while True:
            if self.conn.poll():
                command = self.conn.recv()
                if command == 'stop':
                    print 'stop recording'
                    self.state = self.STATES['STOPPED']
                    break
                elif command == 'record':
                    print 'recording'
                    self.state = self.STATES['RECORDING']
            if self.state == self.STATES['RECORDING']:
                log.times.append(datetime.now())
                frame_periods[record_id] = (log.times[-1] - log.times[-2]).total_seconds()
                frame = self.cam_cap.get_frame()
                if frame:
                    cv.WriteFrame(self.writer, frame)
                    prev_frame = frame
                else:
                    cv.WriteFrame(self.writer, prev_frame)
                record_times_smooth[record_id] = (datetime.now() - log.times[-1]).total_seconds()
                if frame_count > 10:
                    sleep_time = self.frame_period - record_times_smooth[record_id]\
                                    + 0.5 * (self.frame_period - frame_periods.mean())\
                                    - extra_time
                else:
                    sleep_time = self.frame_period - record_times_smooth[record_id]\
                                    - extra_time

                log.sleep_times.append(sleep_time)
                log.record_times.append(record_times_smooth[record_id])
                record_id = (record_id + 1) % avg_count

                if sleep_time > 0:
                    sleep(sleep_time)
                else:
                    print 'warning: recording is lagging'
                frame_count += 1

        log.finish()
        log.print_summary()
        log.save('frame_lengths.dat')
        
        return


class Recorder(object):
    def __init__(self, output_path, cam_cap, fps=24, auto_init=False):
        self.output_path = path(output_path)
        self.fps = fps
        self.cam_cap = cam_cap
        self.conn, self.child_conn = multiprocessing.Pipe()
        if auto_init:
            self.child = self._launch_child()
        else:
            self.child = None

    def _launch_child(self):
        p = multiprocessing.Process(target=self._start_child)
        p.start()
        while True:
            if self.conn.poll():
                response = self.conn.recv()
                if response == 'ready':
                    break
                else:
                    raise Exception('Invalid response from RecorderChild')
            sleep(1. / 100)
        print 'RecorderChild is ready'
        return p

    def _start_child(self):
        child = RecorderChild(self.child_conn, self.output_path, self.cam_cap, self.fps)
        child.main()

    def record(self):
        if self.child is None:
            self.child = self._launch_child()
        print 'request recording: %s' % datetime.now()
        self.conn.send('record')

    def stop(self):
        print 'request stop: %s' % datetime.now()
        self.conn.send('stop')
        if self.child:
            self.child.join()
        del self.child
