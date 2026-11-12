"""Bounded visual marks, never mouse/keyboard commands or desktop coordinates."""
import time

FEATURE = 'screen_annotations_v1'
EVENT = 'screen_annotation'
SCALE = 10000
MAX_POINTS, MAX_SHAPES = 128, 64
COLORS = ('#ff68ad', '#ffc857', '#4cd9ee', '#a5e46b')
SHAPES = ('pen', 'ellipse', 'rectangle')


def valid_annotation(payload):
    if not isinstance(payload, dict) or set(payload) != {'action', 'points', 'color'}:
        return False
    action, points, color = payload['action'], payload['points'], payload['color']
    if not isinstance(action, str) or action not in ('pointer', 'hide', 'clear') + SHAPES:
        return False
    if type(color) is not int or not 0 <= color < len(COLORS) or not isinstance(points, list):
        return False
    minimum, maximum = (1, 1) if action == 'pointer' else (0, 0) if action in ('hide', 'clear') else (2, MAX_POINTS) if action == 'pen' else (2, 2)
    return minimum <= len(points) <= maximum and all(
        isinstance(point, list) and len(point) == 2
        and all(type(v) is int and 0 <= v <= SCALE for v in point) for point in points)


class AnnotationBoard:
    """Ephemeral normalized geometry shared by the preview and desktop overlay."""
    def __init__(self, clock=None):
        self.clock = clock or time.monotonic
        self.shapes, self.pointer = [], None

    def apply(self, payload):
        if not valid_annotation(payload):
            return False
        action, color = payload['action'], payload['color']
        points = tuple(tuple(point) for point in payload['points'])
        if action == 'clear':
            self.shapes.clear(); self.pointer = None
        elif action == 'hide':
            self.pointer = None
        elif action == 'pointer':
            self.pointer = (points[0], color, self.clock())
        else:
            self.shapes.append((action, points, color))
            del self.shapes[:-MAX_SHAPES]
        return True

    def expire_pointer(self):
        if self.pointer and self.clock() - self.pointer[2] > 3:
            self.pointer = None
            return True
        return False

    def clear(self):
        self.shapes.clear(); self.pointer = None
