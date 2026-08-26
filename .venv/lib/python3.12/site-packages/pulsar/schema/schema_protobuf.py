#
# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied.  See the License for the
# specific language governing permissions and limitations
# under the License.
#

import base64
import _pulsar

from .schema import Schema

try:
    from google.protobuf import descriptor_pb2
    from google.protobuf.message import Message as ProtobufMessage
    HAS_PROTOBUF = True
except ImportError:
    HAS_PROTOBUF = False


def _collect_file_descriptors(file_descriptor, visited, file_descriptor_set):
    """Recursively collect all FileDescriptorProto objects into file_descriptor_set."""
    if file_descriptor.name in visited:
        return
    for dep in file_descriptor.dependencies:
        _collect_file_descriptors(dep, visited, file_descriptor_set)
    visited.add(file_descriptor.name)
    proto = descriptor_pb2.FileDescriptorProto()
    file_descriptor.CopyToProto(proto)
    file_descriptor_set.file.append(proto)


def _build_schema_definition(descriptor):
    """
    Build the schema definition dict used by Java's ``ProtobufNativeSchemaData``.

    The returned mapping has these keys:

    .. code-block:: text

        fileDescriptorSet
        rootMessageTypeName
        rootFileDescriptorName

    ``fileDescriptorSet`` contains base64-encoded ``FileDescriptorSet`` bytes.
    This mirrors ``ProtobufNativeSchemaUtils.serialize()`` in the Java client.
    """
    file_descriptor_set = descriptor_pb2.FileDescriptorSet()
    _collect_file_descriptors(descriptor.file, set(), file_descriptor_set)
    file_descriptor_set_bytes = file_descriptor_set.SerializeToString()
    return {
        "fileDescriptorSet": base64.b64encode(file_descriptor_set_bytes).decode('utf-8'),
        "rootMessageTypeName": descriptor.full_name,
        "rootFileDescriptorName": descriptor.file.name,
    }


if HAS_PROTOBUF:
    class ProtobufNativeSchema(Schema):
        """
        Schema for protobuf messages using the native protobuf binary encoding.

        The schema definition is stored as a JSON-encoded ProtobufNativeSchemaData
        (fileDescriptorSet, rootMessageTypeName, rootFileDescriptorName), which is
        compatible with the Java client's ProtobufNativeSchema.

        Parameters
        ----------
        record_cls:
            A generated protobuf message class (subclass of google.protobuf.message.Message).

        Example
        -------
        .. code-block:: python

            import pulsar
            from pulsar.schema import ProtobufNativeSchema
            from my_proto_pb2 import MyMessage

            client = pulsar.Client('pulsar://localhost:6650')
            schema = ProtobufNativeSchema(MyMessage)
            producer = client.create_producer('my-topic', schema=schema)
            consumer = client.subscribe('my-topic', 'my-sub', schema=schema)

            message = MyMessage()
            message.field = 'value'
            producer.send(message)

            received = consumer.receive(timeout_millis=5000)
            typed_value = received.value()
            consumer.acknowledge(received)

            assert isinstance(typed_value, MyMessage)
            assert typed_value.field == 'value'

            consumer.close()
            producer.close()
            client.close()
        """

        def __init__(self, record_cls):
            if not (isinstance(record_cls, type) and issubclass(record_cls, ProtobufMessage)):
                raise TypeError(
                    f'record_cls must be a protobuf Message subclass, got {record_cls!r}'
                )
            schema_definition = _build_schema_definition(record_cls.DESCRIPTOR)
            super(ProtobufNativeSchema, self).__init__(
                record_cls, _pulsar.SchemaType.PROTOBUF_NATIVE, schema_definition, 'PROTOBUF_NATIVE'
            )

        def encode(self, obj):
            self._validate_object_type(obj)
            return obj.SerializeToString()

        def decode(self, data):
            return self._record_cls.FromString(data)

        def __str__(self):
            return f'ProtobufNativeSchema({self._record_cls.__name__})'

else:
    class ProtobufNativeSchema(Schema):
        def __init__(self, _record_cls=None):
            raise Exception(
                "protobuf library support was not found. "
                "Install it with: pip install protobuf"
            )

        def encode(self, obj):
            pass

        def decode(self, data):
            pass
