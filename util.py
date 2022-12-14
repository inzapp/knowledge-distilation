"""
Authors : inzapp

Github url : https://github.com/inzapp/knowledge-distilation

Copyright (c) 2022 Inzapp

Permission is hereby granted, free of charge, to any person obtaining
a copy of this software and associated documentation files (the
"Software"), to deal in the Software without restriction, including
without limitation the rights to use, copy, modify, merge, publish,
distribute, sublicense, and/or sell copies of the Software, and to
permit persons to whom the Software is furnished to do so, subject to
the following conditions:

The above copyright notice and this permission notice shall be
included in all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,
EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF
MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND
NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE
LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION
OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION
WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
"""
import os
from glob import glob
from time import perf_counter

import cv2
import numpy as np
import tensorflow as tf
from keras_flops import get_flops


class ModelUtil:
    def __init__(self):
        pass

    @staticmethod
    def init_image_paths(image_path):
        if image_path.endswith('.txt'):
            with open(image_path, 'rt') as f:
                image_paths = f.readlines()
            for i in range(len(image_paths)):
                image_paths[i] = image_paths[i].replace('\n', '')
        else:
            image_paths = glob(f'{image_path}/**/*.jpg', recursive=True)
        np.random.shuffle(image_paths)
        return image_paths

    @staticmethod
    def set_channel_order(input_shape):
        if input_shape[0] in [1, 3]:
            tf.keras.backend.set_image_data_format('channels_first')
        elif input_shape[2] in [1, 3]:
            tf.keras.backend.set_image_data_format('channels_last')
        else:
            print(f'invalid input shape : {input_shape} input_shape[0] or input_shape[2] value must be 1(gray) or 3(rgb)')
            exit(0)

    @staticmethod
    def get_zero_mod_batch_size(image_paths_length):
        zero_mod_batch_size = 1
        for i in range(1, 256, 1):
            if image_paths_length % i == 0:
                zero_mod_batch_size = i
        return zero_mod_batch_size

    @staticmethod
    def get_gflops(model):
        return get_flops(model, batch_size=1) * 1e-9

    @staticmethod
    def load_img(path, channel):
        color_mode = cv2.IMREAD_COLOR if channel == 3 else cv2.IMREAD_GRAYSCALE
        img = cv2.imdecode(np.fromfile(path, dtype=np.uint8), color_mode)
        raw_bgr = img
        if color_mode == cv2.IMREAD_COLOR:
            img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)  # rb swap
        return img, raw_bgr, path

    @staticmethod
    def resize(img, size):
        img_h, img_w = img.shape[:2]
        if img_h > size[0] or img_w > size[1]:
            img = cv2.resize(img, size, interpolation=cv2.INTER_AREA)
        else:
            img = cv2.resize(img, size, interpolation=cv2.INTER_LINEAR)
        return img

    @staticmethod
    def preprocess(img):
        x = np.asarray(img).astype('float32') / 255.0
        if tf.keras.backend.image_data_format() == 'channels_first':
            if len(img.shape) == 3:
                x = x.transpose(x, (2, 0, 1))
            else:
                x = x.reshape((1,) + img.shape)
        else:
            if len(img.shape) == 2:
                x = x.reshape(img.shape + (1,))
        return x

    @staticmethod
    def get_width_height_channel_from_input_shape(input_shape):
        if input_shape[0] in [1, 3]:
            return input_shape[2], input_shape[1], input_shape[0]
        elif input_shape[2] in [1, 3]:
            return input_shape[1], input_shape[0], input_shape[2]

    @staticmethod
    def check_available_device():
        for d in tf.config.list_physical_devices():
            if str(d).lower().find('gpu') > -1:
                return 'gpu'
        return 'cpu'

    @staticmethod
    @tf.function
    def graph_forward(model, x, device):
        with tf.device(f'/{device}:0'):
            return model(x, training=False)

    @staticmethod
    def check_forwarding_time(model, device):
        input_shape = model.input_shape[1:]
        mul = 1
        for val in input_shape:
            mul *= val

        forward_count = 32
        noise = np.random.uniform(0.0, 1.0, mul * forward_count)
        noise = np.asarray(noise).reshape((forward_count, 1) + input_shape).astype('float32')
        ModelUtil.graph_forward(model, noise[0], device)  # only first forward is slow, skip first forward in check forwarding time

        st = perf_counter()
        for i in range(forward_count):
            ModelUtil.graph_forward(model, noise[i], device)
        et = perf_counter()
        forwarding_time = ((et - st) / forward_count) * 1000.0
        print(f'model forwarding time with {device} : {forwarding_time:.2f} ms')

    @staticmethod
    def nms(y_pred, nms_iou_threshold):
        y_pred = sorted(y_pred, key=lambda x: x['confidence'], reverse=True)
        for i in range(len(y_pred) - 1):
            if y_pred[i]['discard']:
                continue
            for j in range(i + 1, len(y_pred)):
                if y_pred[j]['discard'] or y_pred[i]['class'] != y_pred[j]['class']:
                    continue
                if ModelUtil.iou(y_pred[i]['bbox'], y_pred[j]['bbox']) > nms_iou_threshold:
                    y_pred[j]['discard'] = True

        y_pred_copy = np.asarray(y_pred.copy())
        y_pred = []
        for i in range(len(y_pred_copy)):
            if not y_pred_copy[i]['discard']:
                y_pred.append(y_pred_copy[i])
        return y_pred

    @staticmethod
    def init_class_names(class_names_file_path):
        if os.path.exists(class_names_file_path) and os.path.isfile(class_names_file_path):
            with open(class_names_file_path, 'rt') as classes_file:
                class_names = [s.replace('\n', '') for s in classes_file.readlines()]
                num_classes = len(class_names)
            return class_names, num_classes
        else:
            print(f'class names file dose not exist : {class_names_file_path}')
            print('class file does not exist. the class name will be replaced by the class index and displayed.')
            return [], 0

    @staticmethod
    def iou(a, b):
        """
        Intersection of union function.
        :param a: [x_min, y_min, x_max, y_max] format box a
        :param b: [x_min, y_min, x_max, y_max] format box b
        """
        a_x_min, a_y_min, a_x_max, a_y_max = a
        b_x_min, b_y_min, b_x_max, b_y_max = b
        intersection_width = min(a_x_max, b_x_max) - max(a_x_min, b_x_min)
        intersection_height = min(a_y_max, b_y_max) - max(a_y_min, b_y_min)
        if intersection_width <= 0 or intersection_height <= 0:
            return 0.0
        intersection_area = intersection_width * intersection_height
        a_area = abs((a_x_max - a_x_min) * (a_y_max - a_y_min))
        b_area = abs((b_x_max - b_x_min) * (b_y_max - b_y_min))
        union_area = a_area + b_area - intersection_area
        return intersection_area / (float(union_area) + 1e-5)

    @staticmethod
    def is_background_color_bright(bgr):
        """
        Determine whether the color is bright or not.
        :param bgr: bgr scalar tuple.
        :return: true if parameter color is bright and false if not.
        """
        tmp = np.zeros((1, 1), dtype=np.uint8)
        tmp = cv2.cvtColor(tmp, cv2.COLOR_GRAY2BGR)
        cv2.rectangle(tmp, (0, 0), (1, 1), bgr, -1)
        tmp = cv2.cvtColor(tmp, cv2.COLOR_BGR2GRAY)
        return tmp[0][0] > 127

