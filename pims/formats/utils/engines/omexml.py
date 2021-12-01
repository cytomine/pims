#  * Copyright (c) 2020-2021. Authors: see NOTICE file.
#  *
#  * Licensed under the Apache License, Version 2.0 (the "License");
#  * you may not use this file except in compliance with the License.
#  * You may obtain a copy of the License at
#  *
#  *      http://www.apache.org/licenses/LICENSE-2.0
#  *
#  * Unless required by applicable law or agreed to in writing, software
#  * distributed under the License is distributed on an "AS IS" BASIS,
#  * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  * See the License for the specific language governing permissions and
#  * limitations under the License.

import re
import xml.etree.ElementTree as ElementTree
from io import BytesIO

import numpy as np

NS_ORIGINAL_METADATA = "openmicroscopy.org/OriginalMetadata"
NS_DEFAULT = "http://www.openmicroscopy.org/Schemas/{ns_key}/2016-06"
NS_RE = r"http://www.openmicroscopy.org/Schemas/(?P<ns_key>.*)/[0-9/-]"

#
# These are the OME-XML pixel types - not all supported
#
PT_INT8 = "int8"
PT_INT16 = "int16"
PT_INT32 = "int32"
PT_UINT8 = "uint8"
PT_UINT16 = "uint16"
PT_UINT32 = "uint32"
PT_FLOAT = "float"
PT_BIT = "bit"
PT_DOUBLE = "double"
PT_COMPLEX = "complex"
PT_DOUBLECOMPLEX = "double-complex"
ometypedict = {
    np.dtype(np.int8): PT_INT8,
    np.dtype(np.int16): PT_INT16,
    np.dtype(np.int32): PT_INT32,
    np.dtype(np.uint8): PT_UINT8,
    np.dtype(np.uint16): PT_UINT16,
    np.dtype(np.uint32): PT_UINT32,
    np.dtype(np.float32): PT_FLOAT,
    np.dtype(np.float64): PT_DOUBLE,
    np.dtype(np.complex64): PT_COMPLEX,
    np.dtype(np.complex128): PT_DOUBLECOMPLEX
}


def get_pixel_type(npdtype):
    ptype = ometypedict.get(npdtype)
    if ptype is None:
        raise ValueError('OMEXML get_pixel_type unknown type: ' + npdtype.name)
    return ptype


def get_text(node):
    """Get the contents of text nodes in a parent node"""
    return node.text


def qn(namespace, tag_name):
    """Return the qualified name for a given namespace and tag name

    This is the ElementTree representation of a qualified name
    """
    return "{%s}%s" % (namespace, tag_name)


def split_qn(qn):
    """Split a qualified tag name or return None if namespace not present"""
    m = re.match('\{(.*)\}(.*)', qn)
    if m:
        return m.group(1), m.group(2)
    return None


def get_namespaces(node):
    """Get top-level XML namespaces from a node."""
    ns_lib = {'ome': None, 'sa': None, 'spw': None}
    for child in node.iter():
        nsmatch = split_qn(child.tag)
        if nsmatch is not None:
            ns = nsmatch[0]
            match = re.match(NS_RE, ns)
            if match:
                ns_key = match.group('ns_key').lower()
                ns_lib[ns_key] = ns
    return ns_lib


def get_float_attr(node, attribute, default=None):
    """Cast an element attribute to a float or return None if not present"""
    attr = node.get(attribute)
    return default if attr is None else float(attr)


def get_int_attr(node, attribute):
    """Cast an element attribute to an int or return None if not present"""
    attr = node.get(attribute)
    return None if attr is None else int(attr)


class NodeWrapper:
    def __init__(self, node, root_node):
        self.node = node
        self.root_node = root_node
        self.ns = get_namespaces(self.node)


class OMEXML(object):
    """Reads and writes OME-XML with methods to get and set it.

    The OMEXML class has four main purposes: to parse OME-XML, to output
    OME-XML, to provide a structured mechanism for inspecting OME-XML and to
    let the caller create and modify OME-XML.

    There are two ways to invoke the constructor. If you supply XML as a string
    or unicode string, the constructor will parse it and will use it as the
    base for any inspection and modification. If you don't supply XML, you'll
    get a bland OME-XML object which has a one-channel image. You can modify
    it programatically and get the modified OME-XML back out by calling to_xml.

    There are two ways to get at the XML. The arduous way is to get the
    root_node of the DOM and explore it yourself using the DOM API
    (https://docs.python.org/library/xml.dom.html#module-xml.dom). The easy way,
    where it's supported is to use properties on OMEXML and on some of its
    derived objects.

    See the `OME-XML schema documentation
    <http://git.openmicroscopy.org/src/develop/components/specification/Documentation/Generated/OME-2011-06/ome.html>`.

    """

    def __init__(self, omexml):
        # xml.etree found to be faster than lxml
        from xml.etree import ElementTree as etree  # delayed import

        try:
            self.dom = etree.fromstring(omexml)
        except etree.ParseError as exc:
            omexml = omexml.decode(errors='ignore').encode()
            self.dom = etree.fromstring(omexml)

        # determine OME namespaces
        self.ns = get_namespaces(self.dom)
        if self.ns['ome'] is None:
            raise Exception("Error: String not in OME-XML format")

        self._main_image = None

    def __str__(self):
        for ns_key in ["ome"]:
            ns = self.ns.get(ns_key) or NS_DEFAULT.format(ns_key=ns_key)
            ElementTree.register_namespace('', ns)
        result = BytesIO()
        ElementTree.ElementTree(self.root_node).write(
            result, encoding='utf-8', method="xml", xml_declaration=True
        )
        return result.getvalue().decode()

    def to_xml(self):
        return str(self)

    def get_ns(self, key):
        return self.ns[key]

    @property
    def root_node(self):
        return self.dom

    @property
    def image_count(self):
        """The number of images (= series) specified by the XML"""
        return len(self.root_node.findall(qn(self.ns['ome'], "Image")))

    @property
    def structured_annotations(self):
        """Return the structured annotations container

        returns a wrapping of OME/StructuredAnnotations. It creates
        the element if it doesn't exist.
        """
        node = self.root_node.find(qn(self.ns['sa'], "StructuredAnnotations"))
        if node is None:
            node = ElementTree.SubElement(
                self.root_node, qn(self.ns['sa'], "StructuredAnnotations")
            )
        return self.StructuredAnnotations(node)

    class Instrument(NodeWrapper):
        @property
        def id(self):
            return self.node.get("ID")

        @property
        def microscope(self):
            """The OME/Image/Pixels element.
            """
            node = self.node.find(qn(self.ns['ome'], "Microscope"))
            if node:
                return OMEXML.Microscope(node, self.root_node)
            return None

    class Microscope(NodeWrapper):
        @property
        def model(self):
            return self.node.get("Model")

        @property
        def manufacturer(self):
            return self.node.get("Manufacturer")

    class Objective(NodeWrapper):
        @property
        def id(self):
            return self.node.get("ID")

        @property
        def nominal_magnification(self):
            return self.node.get("NominalMagnification")

        @property
        def calibrated_magnification(self):
            return self.node.get("CalibratedMagnification")

    class Image(NodeWrapper):
        @property
        def id(self):
            return self.node.get("ID")

        @property
        def name(self):
            return self.node.get("Name")

        @property
        def description(self):
            node = self.node.find(qn(self.ns["ome"], "Description"))
            if node is None:
                return None
            return get_text(node)

        @property
        def acquisition_date(self):
            """The date in ISO-8601 format"""
            acquired_date = self.node.find(qn(self.ns["ome"], "AcquisitionDate"))
            if acquired_date is None:
                return None
            return get_text(acquired_date)

        @property
        def pixels(self):
            return OMEXML.Pixels(self.node.find(qn(self.ns['ome'], "Pixels")), self.root_node)

        @property
        def instrument(self):
            ref = self.node.find(qn(self.ns['ome'], "InstrumentRef"))
            if ref is not None:
                node = self.root_node.find(
                    qn(self.ns['ome'], f"Instrument[@ID='{ref.get('ID')}']")
                )
                return OMEXML.Instrument(node, self.root_node)
            return None

        @property
        def objective(self):
            ref = self.node.find(qn(self.ns['ome'], "ObjectiveSettings"))
            if ref is not None:
                node = self.instrument.node.find(
                    qn(self.ns['ome'], f"Objective[@ID='{ref.get('ID')}']")
                )
                return OMEXML.Objective(node, self.root_node)
            return None

    def image(self, index=0) -> Image:
        """Return an image node by index"""
        return self.Image(
            self.root_node.findall(qn(self.ns['ome'], "Image"))[index], self.root_node
        )

    def image_by_name(self, name, case_sensitive=False):
        for i in range(self.image_count):
            image = self.image(i)
            if (case_sensitive and image.name == name) or \
                    (not case_sensitive and image.name is not None and
                     image.name.lower() == name.lower()):
                return image

    @property
    def main_image(self) -> Image:
        if self._main_image is not None:
            return self._main_image
        for i in range(self.image_count):
            image = self.image(i)
            if image.name and \
                    image.name.lower() in ['label', 'macro', 'thumbnail']:
                continue
            else:
                self._main_image = image
                return image

    class Channel(NodeWrapper):
        @property
        def id(self):
            return self.node.get("ID")

        @property
        def name(self):
            return self.node.get("Name")

        @property
        def samples_per_pixels(self):
            return get_int_attr(self.node, "SamplesPerPixel")

        @property
        def color(self):
            return get_int_attr(self.node, "Color")

        @property
        def emission_wavelength(self):
            return get_float_attr(self.node, "EmissionWavelength")

        @property
        def excitation_wavelength(self):
            return get_float_attr(self.node, "ExcitationWavelength")

    class TiffData(NodeWrapper):
        @property
        def first_z(self):
            """The Z index of the plane"""
            return get_int_attr(self.node, "FirstZ")

        @property
        def first_c(self):
            """The channel index of the plane"""
            return get_int_attr(self.node, "FirstC")

        @property
        def first_t(self):
            """The T index of the plane"""
            return get_int_attr(self.node, "FirstT")

        @property
        def ifd(self):
            """plane index within tiff file"""
            return get_int_attr(self.node, "IFD")

        @property
        def plane_count(self):
            """How many planes in this TiffData. Should always be 1"""
            return get_int_attr(self.node, "PlaneCount")

    class Plane(NodeWrapper):
        """The OME/Image/Pixels/Plane element

        The Plane element represents one 2-dimensional image plane. It
        has the Z, C and T indices of the plane and optionally has the
        X, Y, Z, exposure time and a relative time delta.
        """

        @property
        def the_Z(self):
            """The Z index of the plane"""
            return get_int_attr(self.node, "TheZ")

        @property
        def the_C(self):
            """The channel index of the plane"""
            return get_int_attr(self.node, "TheC")

        @property
        def the_T(self):
            """The T index of the plane"""
            return get_int_attr(self.node, "TheT")

        @property
        def delta_T(self):
            """# of seconds since the beginning of the experiment"""
            return get_float_attr(self.node, "DeltaT")

        @property
        def exposure_time(self):
            """Units are seconds. Duration of acquisition????"""
            return get_float_attr(self.node, "ExposureTime")

        @property
        def position_X(self):
            """X position of stage"""
            return get_float_attr(self.node, "PositionY")

        @property
        def position_Y(self):
            """Y position of stage"""
            return get_float_attr(self.node, "PositionY")

        @property
        def position_Z(self):
            """Z position of stage"""
            return get_float_attr(self.node, "PositionZ")

    class Pixels(NodeWrapper):
        """The OME/Image/Pixels element

        The Pixels element represents the pixels in an OME image and, for
        an OME-XML encoded image, will actually contain the base-64 encoded
        pixel data. It has the X, Y, Z, C, and T extents of the image
        and it specifies the channel interleaving and channel depth.
        """

        @property
        def id(self):
            return self.node.get("ID")

        @property
        def dimension_order(self):
            """The ordering of image planes in the file

            A 5-letter code indicating the ordering of pixels, from the most
            rapidly varying to least. Use the DO_* constants (for instance
            DO_XYZCT) to compare and set this.
            """
            return self.node.get("DimensionOrder")

        @property
        def pixel_type(self):
            """The pixel bit type, for instance PT_UINT8

            The pixel type specifies the datatype used to encode pixels
            in the image data. You can use the PT_* constants to compare
            and set the pixel type.
            """
            return self.node.get("Type")

        @property
        def size_X(self):
            """The dimensions of the image in the X direction in pixels"""
            return get_int_attr(self.node, "SizeX")

        @property
        def size_Y(self):
            """The dimensions of the image in the Y direction in pixels"""
            return get_int_attr(self.node, "SizeY")

        @property
        def size_Z(self):
            """The dimensions of the image in the Z direction in pixels"""
            return get_int_attr(self.node, "SizeZ")

        @property
        def size_T(self):
            """The dimensions of the image in the T direction in pixels"""
            return get_int_attr(self.node, "SizeT")

        @property
        def size_C(self):
            """The dimensions of the image in the C direction in pixels"""
            return get_int_attr(self.node, "SizeC")

        @property
        def physical_size_X(self):
            """The dimensions of the image in the X direction in physical units"""
            return get_float_attr(self.node, "PhysicalSizeX")

        @property
        def physical_size_X_unit(self):
            return self.node.get("PhysicalSizeXUnit", "µm")

        @property
        def physical_size_Y(self):
            """The dimensions of the image in the Y direction in physical units"""
            return get_float_attr(self.node, "PhysicalSizeY")

        @property
        def physical_size_Y_unit(self):
            return self.node.get("PhysicalSizeYUnit", "µm")

        @property
        def physical_size_Z(self):
            """The dimensions of the image in the Z direction in physical units"""
            return get_float_attr(self.node, "PhysicalSizeZ")

        @property
        def physical_size_Z_unit(self):
            return self.node.get("PhysicalSizeZUnit", "µm")

        @property
        def time_increment(self):
            return get_float_attr(self.node, "TimeIncrement")

        @property
        def time_increment_unit(self):
            return self.node.get("TimeIncrementUnit", "s")

        @property
        def channel_count(self):
            """The number of channels in the image

            You can change the number of channels in the image by
            setting the channel_count:

            pixels.channel_count = 3
            pixels.channel(0).Name = "Red"
            ...
            """
            return len(self.node.findall(qn(self.ns['ome'], "Channel")))

        def get_channel_names(self):
            return [self.channel(i).name for i in range(self.channel_count)]

        def channel(self, index=0):
            """Get the indexed channel from the Pixels element"""
            channel = self.node.findall(qn(self.ns['ome'], "Channel"))[index]
            return OMEXML.Channel(channel, self.root_node)

        @property
        def plane_count(self):
            """The number of planes in the image

            An image with only one plane or an interleaved color plane will
            often not have any planes.

            You can change the number of planes in the image by
            setting the plane_count:

            pixels.plane_count = 3
            pixels.Plane(0).TheZ=pixels.Plane(0).TheC=pixels.Plane(0).TheT=0
            ...
            """
            return len(self.node.findall(qn(self.ns['ome'], "Plane")))

        def Plane(self, index=0):
            """Get the indexed plane from the Pixels element"""
            plane = self.node.findall(qn(self.ns['ome'], "Plane"))[index]
            return OMEXML.Plane(plane, self.root_node)

        @property
        def tiff_data_count(self):
            return len(self.node.findall(qn(self.ns['ome'], "TiffData")))

        def tiff_data(self, index=0):
            """Get the indexed TiffData from the Pixels element"""
            tiff_data = self.node.findall(qn(self.ns['ome'], "TiffData"))[index]
            return OMEXML.TiffData(tiff_data, self.root_node)

        def get_planes_of_channel(self, index):
            planes = self.node.findall(qn(self.ns['ome'], "Plane[@TheC='" + str(index) + "']"))
            return [OMEXML.Plane(plane, self.root_node) for plane in planes]

    class StructuredAnnotations(dict):
        """The OME/StructuredAnnotations element

        Structured annotations let OME-XML represent metadata from other file
        formats, for example the tag metadata in TIFF files. The
        StructuredAnnotations element is a container for the structured
        annotations.

        Images can have structured annotation references. These match to
        the IDs of structured annotations in the StructuredAnnotations
        element. You can get the structured annotations in an OME-XML document
        using a dictionary interface to StructuredAnnotations.

        Pragmatically, TIFF tag metadata is stored as key/value pairs in
        OriginalMetadata annotations - in the context of CellProfiler,
        callers will be using these to read tag data that's not represented
        in OME-XML such as the bits per sample and min and max sample values.

        """

        def __init__(self, node):
            self.node = node
            self.ns = get_namespaces(self.node)

        def __getitem__(self, key):
            for child in self.node:
                if child.get("ID") == key:
                    return child
            raise IndexError('ID "%s" not found' % key)

        def __contains__(self, key):
            return self.has_key(key)

        def keys(self):
            return filter(
                lambda x: x is not None,
                [child.get("ID") for child in self.node]
            )

        def has_key(self, key):
            for child in self.node:
                if child.get("ID") == key:
                    return True
            return False

        def iter_original_metadata(self):
            """An iterator over the original metadata in structured annotations

            returns (<annotation ID>, (<key, value>))

            where <annotation ID> is the ID attribute of the annotation (which
            can be used to tie an annotation to an image)

                  <key> is the original metadata key, typically one of the
                  OM_* names of a TIFF tag
                  <value> is the value for the metadata
            """
            #
            # Here's the XML we're traversing:
            #
            # <StructuredAnnotations>
            #    <XMLAnnotation>
            #        <Value>
            #            <OriginalMetadta>
            #                <Key>Foo</Key>
            #                <Value>Bar</Value>
            #            </OriginalMetadata>
            #        </Value>
            #    </XMLAnnotation>
            # </StructuredAnnotations>
            #
            for annotation_node in self.node.findall(qn(self.ns['sa'], "XMLAnnotation")):
                # <XMLAnnotation/>
                annotation_id = annotation_node.get("ID")
                for xa_value_node in annotation_node.findall(qn(self.ns['sa'], "Value")):
                    # <Value/>
                    for om_node in xa_value_node.findall(
                            qn(NS_ORIGINAL_METADATA, "OriginalMetadata")
                    ):
                        # <OriginalMetadata>
                        key_node = om_node.find(qn(NS_ORIGINAL_METADATA, "Key"))
                        value_node = om_node.find(qn(NS_ORIGINAL_METADATA, "Value"))
                        if key_node is not None and value_node is not None:
                            key_text = get_text(key_node)
                            value_text = get_text(value_node)
                            if key_text is not None and value_text is not None:
                                yield annotation_id, (key_text, value_text)
            return

        def has_original_metadata(self, key):
            """True if there is an original metadata item with the given key"""
            return any(
                [k == key
                 for annotation_id, (k, v)
                 in self.iter_original_metadata()]
            )

        def get_original_metadata_value(self, key, default=None):
            """Return the value for a particular original metadata key

            key - key to search for
            default - default value to return if not found
            """
            for annotation_id, (k, v) in self.iter_original_metadata():
                if k == key:
                    return v
            return default

        def get_original_metadata_refs(self, ids):
            """For a given ID, get the matching original metadata references

            ids - collection of IDs to match

            returns a dictionary of key to value
            """
            d = {}
            for annotation_id, (k, v) in self.iter_original_metadata():
                if annotation_id in ids:
                    d[k] = v
            return d

        @property
        def OriginalMetadata(self):
            return OMEXML.OriginalMetadata(self)

    class OriginalMetadata(dict):
        """View original metadata as a dictionary

        Original metadata holds "vendor-specific" metadata including TIFF
        tag values.
        """

        def __init__(self, sa):
            """Initialized with the structured_annotations class instance"""
            self.sa = sa

        def __getitem__(self, key):
            return self.sa.get_original_metadata_value(key)

        def __setitem__(self, key, value):
            self.sa.add_original_metadata(key, value)

        def __contains__(self, key):
            return self.has_key(key)

        def __iter__(self):
            for annotation_id, (key, value) in self.sa.iter_original_metadata():
                yield key

        def __len__(self):
            return len(list(self.sa.iter_original_metadata()))

        def keys(self):
            return [key
                    for annotation_id, (key, value)
                    in self.sa.iter_original_metadata()]

        def has_key(self, key):
            for annotation_id, (k, value) in self.sa.iter_original_metadata():
                if k == key:
                    return True
            return False

        def iteritems(self):
            for annotation_id, (key, value) in self.sa.iter_original_metadata():
                yield key, value
