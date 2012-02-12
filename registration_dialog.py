from __future__ import division
import os
import sys
import random

import gtk
import numpy as np
from path import path
from pygtkhelpers.ui.dialogs import yesno

from safe_cv import cv
from overlay_registration import ImageRegistrationTask, Point, OVERLAY_CLICK,\
                                IMAGE_CLICK, CANCEL


class RegistrationDialog(object):
    def __init__(self):
        self.builder = gtk.Builder()
        self.builder.add_from_file(self.get_glade_path())
        self.window = self.builder.get_object('dialog')
        self.label_info = self.builder.get_object('label_info')
        self.areas = dict(
            original=self.builder.get_object('original'),
            rotated=self.builder.get_object('rotated'),
            result=self.builder.get_object('result'))
        # connect signals from glade to python
        self.builder.connect_signals(self)
        # show window and contents
        self.images = {}
        self.pixmaps = {}
        self.pixbufs = {}
        self.window.show_all()
        self.registration = ImageRegistrationTask(
                    on_overlay_point=lambda x: self.label_info.set_text(x),
                    on_image_point=lambda x: self.label_info.set_text(x),
                    on_registered=self.on_image_registered,
                    on_canceled=self.on_canceled)

    def run(self):
        self.reset()
        response = self.window.run()
        if response == gtk.RESPONSE_OK:
            results = self.registration.map_mat
        else:
            results = None
        self.window.hide()
        return results

    def get_glade_path(self):
        return path('glade').joinpath('registration_demo.glade')

    def translate_coords(self, coords, name):
        coords = Point(*coords)
        width = self.images[name].width
        height = self.images[name].height
        x, y, area_width, area_height = self.areas[name].get_allocation()
        return Point(coords.x / area_width * width,
                coords.y / area_height * height)

    def get_original_image(self):
        raise NotImplementedError

    def get_rotated_image(self):
        raise NotImplementedError

    def reset(self):
        self.images['original'] = self.get_original_image()
        self.images['rotated'] = self.get_rotated_image()

        for i in ['original', 'rotated']:
            self.draw_cv_to_pixmap(i)
            self.areas[i].queue_draw()
        self.registration.start()

    def on_canceled(self, *args):
        for i in ['original', 'rotated']:
            self.areas[i].queue_draw()

    def on_image_registered(self, *args):
        # Image has been registered, apply transformation.
        self.images['result'] = self.registration.get_corrected_image(self.images['rotated'])
        self.draw_cv_to_pixmap('result')
        #for i in ['original', 'rotated', 'result']:
        for i in ['result']:
            self.areas[i].queue_draw()
        self.label_info.set_text('')

    def make_event(self, etype, **kwargs):
        event = state.Event(etype)
        for key, value in kwargs.iteritems():
            setattr(event, key, value)
        return event

    def draw_cv_to_pixmap(self, image_name):
        image = self.images[image_name]
        x, y, width, height = self.areas[image_name].get_allocation()
        if image.width != width or image.height != height:
            image = self.get_resized(image, width, height)
        self.pixbufs[image_name] = gtk.gdk.pixbuf_new_from_data(
            image.tostring(), gtk.gdk.COLORSPACE_RGB, False,
            8, width, height, width * 3)
        self.pixmaps[image_name], mask =\
            self.pixbufs[image_name].render_pixmap_and_mask()
    
    def get_resized(self, in_image, width, height):
        #print 'get_resized width=%s height=%s'\
            #% (width, height)
        resized = cv.CreateImage((width, height), 8, in_image.channels)
        cv.Resize(in_image, resized)
        return cv.GetMat(resized)

    def on_original_expose_event(self, widget, event):
        x , y, width, height = event.area
        if 'original' not in self.pixmaps:
            return False
        self.areas['original'].window\
            .draw_drawable(self.areas['original'].get_style().white_gc,
                    self.pixmaps['original'], x, y, x, y, width, height)
        return False

    def on_rotated_expose_event(self, widget, event):
        if 'rotated' not in self.pixmaps:
            return False
        x , y, width, height = event.area
        self.areas['rotated'].window\
            .draw_drawable(self.areas['rotated'].get_style().white_gc,
                    self.pixmaps['rotated'], x, y, x, y, width, height)
        return False

    def on_result_expose_event(self, widget, event):
        if 'result' not in self.pixmaps:
            return False
        x , y, width, height = event.area
        self.areas['result'].window\
            .draw_drawable(self.areas['result'].get_style().white_gc,
                    self.pixmaps['result'], x, y, x, y, width, height)
        return False

    def on_button_reset_clicked(self, *args, **kwargs):
        self.reset()

    def on_rotated_button_press_event(self, widget, event):
        self.registration.trigger_event(IMAGE_CLICK,
            cairo_context=widget.window.cairo_create(),
            point=Point(*self.translate_coords(event.get_coords(), 'rotated')),
            cairo_point=Point(*event.get_coords()))
        return False

    def on_original_button_press_event(self, widget, event):
        self.registration.trigger_event(OVERLAY_CLICK,
            cairo_context=widget.window.cairo_create(),
            point=Point(*self.translate_coords(event.get_coords(), 'original')),
            cairo_point=Point(*event.get_coords()))
        return False