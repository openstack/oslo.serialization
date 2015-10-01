# Copyright 2015 Red Hat
# All Rights Reserved.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

from oslo_serialization import base64
from oslotest import base as test_base


class Base64Tests(test_base.BaseTestCase):

    def test_encode_as_bytes(self):
        self.assertEqual(base64.encode_as_bytes(b'text'),
                         b'dGV4dA==')
        self.assertEqual(base64.encode_as_bytes(u'text'),
                         b'dGV4dA==')
        self.assertEqual(base64.encode_as_bytes(u'e:\xe9'),
                         b'ZTrDqQ==')
        self.assertEqual(base64.encode_as_bytes(u'e:\xe9', encoding='latin1'),
                         b'ZTrp')

    def test_encode_as_text(self):
        self.assertEqual(base64.encode_as_text(b'text'),
                         u'dGV4dA==')
        self.assertEqual(base64.encode_as_text(u'text'),
                         u'dGV4dA==')
        self.assertEqual(base64.encode_as_text(u'e:\xe9'),
                         u'ZTrDqQ==')
        self.assertEqual(base64.encode_as_text(u'e:\xe9', encoding='latin1'),
                         u'ZTrp')

    def test_decode_as_bytes(self):
        self.assertEqual(base64.decode_as_bytes(b'dGV4dA=='),
                         b'text')
        self.assertEqual(base64.decode_as_bytes(u'dGV4dA=='),
                         b'text')

    def test_decode_as_text(self):
        self.assertEqual(base64.decode_as_text(b'dGV4dA=='),
                         u'text')
        self.assertEqual(base64.decode_as_text(u'dGV4dA=='),
                         u'text')
        self.assertEqual(base64.decode_as_text(u'ZTrDqQ=='),
                         u'e:\xe9')
        self.assertEqual(base64.decode_as_text(u'ZTrp', encoding='latin1'),
                         u'e:\xe9')
