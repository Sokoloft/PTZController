import unittest
from pathlib import Path
import xml.etree.ElementTree as ET
from ptz_controller.controllers import face_labels


class FaceLabelTests(unittest.TestCase):
    def test_xbox_uses_sdl_binding_instead_of_raw_index(self):
        labels = face_labels('Xbox 360 Controller', '', {'a':'b4','b':'b2','x':'b0','y':'b1'})
        self.assertEqual(labels['button:4'], {'label':'A','symbol':'A','style':'xbox-a'})
        self.assertEqual(labels['button:0']['label'], 'X')
        self.assertEqual(labels['button:2']['style'], 'xbox-b')

    def test_sony_uses_cross_circle_square_triangle(self):
        labels = face_labels('DualSense Wireless Controller', '', {'a':'b1','b':'b2','x':'b0','y':'b3'})
        self.assertEqual(labels['button:1'], {'label':'Cross','symbol':'×','style':'sony-cross'})
        self.assertEqual(labels['button:0']['symbol'], '□')
        self.assertEqual(labels['button:2']['symbol'], '○')
        self.assertEqual(labels['button:3']['symbol'], '△')

    def test_vendor_identification_and_unknown_fallback(self):
        guid = '030000004c050000cc09000011010000'
        self.assertEqual(face_labels('Wireless Controller', guid, {'a':'b0'})['button:0']['style'], 'sony-cross')
        self.assertEqual(face_labels('Generic gamepad', '', {'a':'b0'}), {})
        self.assertEqual(face_labels('Xbox Controller', '', {}), {})
        self.assertEqual(face_labels('Sony Controller', '', {'a':'a0'}), {})

    def test_all_xbox_controls_and_hat_bitmasks(self):
        mapping = dict(zip(('a','b','x','y','leftshoulder','rightshoulder','back','start',
                            'leftstick','rightstick','guide'), (f'b{i}' for i in range(11))))
        mapping.update(dpup='h0.1', dpright='h0.2', dpdown='h0.4', dpleft='h0.8')
        labels = face_labels('Xbox 360 Controller', '', mapping, 11, 1)
        self.assertEqual(len(labels), 15)
        self.assertEqual(labels['button:4']['symbol'], 'LB')
        self.assertEqual(labels['button:5']['symbol'], 'RB')
        self.assertEqual(labels['button:6']['label'], 'Back / Select')
        self.assertEqual(labels['button:7']['label'], 'Start')
        self.assertEqual(labels['button:8']['icon'], 'xbox-leftstick')
        self.assertEqual(labels['button:9']['icon'], 'xbox-rightstick')
        self.assertEqual(labels['button:10']['icon'], 'xbox-guide')
        for direction in ('up', 'right', 'down', 'left'):
            self.assertEqual(labels[f'hat:0:{direction}']['icon'], 'xbox-dpad-' + direction)

    def test_sony_controls_model_labels_and_button_dpad(self):
        mapping = {'leftshoulder':'b4','rightshoulder':'b5','leftstick':'b10','rightstick':'b11',
                   'back':'b8','start':'b9','guide':'b12','dpup':'b13','touchpad':'b14','misc1':'b15'}
        for name, back, start in (('DualShock 3','Select','Start'),('DualShock 4','Share','Options'),
                                 ('DualSense','Create','Options')):
            with self.subTest(name=name):
                labels = face_labels(name, '', mapping, 16, 0)
                self.assertEqual(labels['button:4']['symbol'], 'L1')
                self.assertEqual(labels['button:5']['symbol'], 'R1')
                self.assertEqual(labels['button:8']['label'], back)
                self.assertEqual(labels['button:9']['label'], start)
                self.assertEqual(labels['button:10']['symbol'], 'L3')
                self.assertEqual(labels['button:11']['symbol'], 'R3')
                self.assertEqual(labels['button:12']['icon'], 'sony-guide')
                self.assertEqual(labels['button:13']['icon'], 'sony-dpad-up')
                self.assertEqual(labels['button:14']['icon'], 'sony-touchpad')
        self.assertEqual(labels['button:15']['icon'], 'sony-mute')

    def test_extra_buttons_have_icons_and_every_svg_reference_exists(self):
        mapping = {'lefttrigger':'b0','righttrigger':'b1','misc1':'b2','touchpad':'b3',
                   **{f'paddle{i}':f'b{i+3}' for i in range(1,5)}}
        svg = ET.parse(Path(__file__).resolve().parents[1]/'src/ptz_controller/static/controller-icons.svg')
        ids = {node.attrib['id'] for node in svg.iter() if 'id' in node.attrib}
        for name in ('Xbox 360','Xbox Series Controller','DualSense','DualShock 4','DualShock 3'):
            labels = face_labels(name, '', mapping, 12, 2)
            self.assertEqual(len(labels), 20)
            self.assertEqual(labels['button:11']['symbol'], 'B11')
            for source, label in labels.items():
                self.assertTrue(label['symbol'], source)
                if 'icon' in label:self.assertIn(label['icon'], ids)
        self.assertEqual(face_labels('Unknown joystick','',mapping,12,2), {})
