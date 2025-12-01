#    Copyright 2016 Mirantis, Inc.
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

from typing import Any

from oslo_serialization import msgpackutils
from oslo_serialization.serializer.base_serializer import BaseSerializer
from oslo_serialization._types import ReadableStream, SupportsWrite


class MessagePackSerializer(BaseSerializer):
    """MessagePack serializer based on the msgpackutils module."""

    def __init__(self, registry: Any = None) -> None:
        self._registry = registry

    def dump(self, obj: Any, fp: SupportsWrite) -> None:
        return msgpackutils.dump(obj, fp, registry=self._registry)

    def dump_as_bytes(self, obj: Any) -> bytes:
        return msgpackutils.dumps(obj, registry=self._registry)

    def load(self, fp: ReadableStream) -> Any:
        return msgpackutils.load(fp, registry=self._registry)

    def load_from_bytes(self, s: bytes) -> Any:
        return msgpackutils.loads(s, registry=self._registry)
