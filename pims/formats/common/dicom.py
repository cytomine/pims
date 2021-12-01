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
import logging
from datetime import datetime
from functools import cached_property
from typing import List, Optional, Union

import numpy as np
from pint import Quantity
from pydicom import FileDataset, dcmread
from pydicom.dicomdir import DicomDir
from pydicom.multival import MultiValue
from shapely.errors import WKTReadingError
from shapely.wkt import loads as wkt_loads

from pims.formats.utils.abstract import (
    AbstractFormat, CachedDataPath
)
from pims.formats.utils.checker import SignatureChecker
from pims.formats.utils.engines.vips import VipsSpatialConvertor
from pims.formats.utils.histogram import DefaultHistogramReader
from pims.formats.utils.parser import AbstractParser
from pims.formats.utils.reader import AbstractReader
from pims.formats.utils.structures.annotations import ParsedMetadataAnnotation
from pims.formats.utils.structures.metadata import ImageChannel, ImageMetadata, MetadataStore
from pims.processing.adapters import numpy_to_vips
from pims.utils import UNIT_REGISTRY
from pims.utils.arrays import to_unsigned_int
from pims.utils.dtypes import np_dtype
from pims.utils.types import parse_float

log = logging.getLogger("pims.formats")


def cached_dcmread(format: AbstractFormat) -> Union[FileDataset, DicomDir]:
    return format.get_cached(
        '_dcmread', dcmread, format.path.resolve(), force=True
    )


class DicomChecker(SignatureChecker):
    OFFSET = 128

    @classmethod
    def match(cls, pathlike: CachedDataPath) -> bool:
        buf = cls.get_signature(pathlike)
        return (len(buf) > cls.OFFSET + 4 and
                buf[cls.OFFSET] == 0x44 and
                buf[cls.OFFSET + 1] == 0x49 and
                buf[cls.OFFSET + 2] == 0x43 and
                buf[cls.OFFSET + 3] == 0x4D)


class DicomParser(AbstractParser):
    def parse_main_metadata(self) -> ImageMetadata:
        ds = cached_dcmread(self.format)

        imd = ImageMetadata()
        imd.width = ds.Columns
        imd.height = ds.Rows
        imd.depth = ds.get('NumberOfFrames', 1)
        imd.duration = 1

        imd.n_channels = ds.SamplesPerPixel  # Only 1 or 3
        imd.n_intrinsic_channels = ds.SamplesPerPixel
        imd.n_channels_per_read = 1
        if imd.n_channels == 3:
            imd.set_channel(ImageChannel(index=0, suggested_name='R'))
            imd.set_channel(ImageChannel(index=1, suggested_name='G'))
            imd.set_channel(ImageChannel(index=2, suggested_name='B'))
        else:
            imd.set_channel(ImageChannel(index=0, suggested_name='L'))

        imd.significant_bits = ds.BitsAllocated
        imd.pixel_type = np_dtype(imd.significant_bits)
        return imd

    def parse_known_metadata(self) -> ImageMetadata:
        ds = cached_dcmread(self.format)
        imd = super().parse_known_metadata()

        imd.description = None
        imd.acquisition_datetime = self.parse_acquisition_date(
            ds.get('AcquisitionDate'), ds.get('AcquisitionTime')
        )
        if imd.acquisition_datetime is None:
            imd.acquisition_datetime = self.parse_acquisition_date(
                ds.get('ContentDate'), ds.get('ContentTime')
            )
        pixel_spacing = ds.get('PixelSpacing')
        if pixel_spacing:
            imd.physical_size_x = self.parse_physical_size(pixel_spacing[0])
            imd.physical_size_y = self.parse_physical_size(pixel_spacing[1])
        imd.physical_size_z = self.parse_physical_size(
            ds.get('SpacingBetweenSlices')
        )

        imd.is_complete = True
        return imd

    @staticmethod
    def parse_acquisition_date(
        date: str, time: Optional[str] = None
    ) -> Optional[datetime]:
        """
        Date examples: 20211105
        Time examples: 151034, 151034.123
        """
        try:
            if date and time:
                return datetime.strptime(
                    f"{date} {time.split('.')[0]}", "%Y%m%d %H%M%S"
                )
            elif date:
                return datetime.strptime(date, "%Y%m%d")
            else:
                return None
        except (ValueError, TypeError):
            return None

    @staticmethod
    def parse_physical_size(physical_size: str) -> Optional[Quantity]:
        if physical_size is not None and parse_float(physical_size) is not None:
            return parse_float(physical_size) * UNIT_REGISTRY("millimeter")
        return None

    def parse_raw_metadata(self) -> MetadataStore:
        store = super(DicomParser, self).parse_raw_metadata()
        ds = cached_dcmread(self.format)

        for data_element in ds:
            if type(data_element.value) in (bytes, bytearray) \
                    or data_element.VR == "SQ":
                # TODO: support sequence metadata
                continue

            name = data_element.name
            if data_element.is_private:
                tag = data_element.tag
                name = f"{tag.group:04x}_{tag.element:04x}"  # noqa
            name = name.replace(' ', '')

            value = data_element.value
            if type(value) is MultiValue:
                value = list(value)
            store.set(name, value, namespace="DICOM")
        return store

    def parse_annotations(self) -> List[ParsedMetadataAnnotation]:
        """
        DICOM/DICONDE extension for Annotations
        * 0x0077-0x1900 (US) - Annotation.Number
        * 0x0077-0x1901 (SQ) - Annotation.Definition
        * 0x0077-0x1912 (DS, multiple) - Annotation.Row
        * 0x0077-0x1913 (DS, multiple) - Annotation.Col
        * 0x0077-0x1903 (LO) - Annotation.Indication
        * 0x0077-0x1904 (US) - Annotation.Severity
        * 0x0077-0x1911 (LT) - Annotation.Polygon (WKT format)
        """
        ds = cached_dcmread(self.format)
        channels = list(range(self.format.main_imd.n_channels))
        parsed_annots = []
        annots_sq = ds.get((0x77, 0x1901))
        if annots_sq and annots_sq.VR == "SQ":
            for annot in annots_sq:
                try:
                    wkt = annot.get((0x77, 0x1911))
                    if wkt.value is not None:
                        geometry = wkt_loads(wkt.value)
                        parsed = ParsedMetadataAnnotation(
                            geometry, channels, 0, 0
                        )

                        indication = annot.get((0x77, 0x1903))
                        if indication:
                            parsed.add_term(indication.value)

                        severity = annot.get((0x77, 0x1904))
                        if severity:
                            parsed.add_property("severity", severity.value)

                        parsed_annots.append(parsed)
                except WKTReadingError:
                    pass

        return parsed_annots


class DicomReader(AbstractReader):
    """
    Pydicom `pixel_array` have following shape:
    * For single frame, single sample data (rows, columns)
    * For single frame, multi-sample data (rows, columns, planes)
    * For multi-frame, single sample data (frames, rows, columns)
    * For multi-frame, multi-sample data (frames, rows, columns, planes)

    NB: in DICOM world, "frame" is a z-slice and "plane" is a channel.
    """

    def _array_slices(self, x_np_slice, y_np_slice, c_np_slice, z_np_slice):
        n_channels = self.format.main_imd.n_channels
        depth = self.format.main_imd.depth

        x_np_slice = np.s_[:] if x_np_slice is None else x_np_slice
        y_np_slice = np.s_[:] if y_np_slice is None else y_np_slice
        c_np_slice = np.s_[:] if c_np_slice is None else c_np_slice
        z_np_slice = np.s_[:] if z_np_slice is None else z_np_slice

        if n_channels == 1 and depth == 1:
            return y_np_slice, x_np_slice
        elif n_channels > 1 and depth == 1:
            return y_np_slice, x_np_slice, c_np_slice
        elif n_channels == 1 and depth > 1:
            return z_np_slice, y_np_slice, x_np_slice
        else:
            return z_np_slice, y_np_slice, x_np_slice, c_np_slice

    def _pixel_array(self, x_np_slice, y_np_slice, c_np_slice, z_np_slice):
        slices = self._array_slices(x_np_slice, y_np_slice, c_np_slice, z_np_slice)
        ds = cached_dcmread(self.format)
        image = ds.pixel_array[slices]
        return to_unsigned_int(image)

    def read_thumb(
        self, out_width, out_height, precomputed=None,
        c=None, z=None, t=None
    ):
        return self._pixel_array(None, None, c, z)

    def read_window(
        self, region, out_width, out_height,
        c=None, z=None, t=None
    ):
        region = region.scale_to_tier(self.format.pyramid.base)
        return self._pixel_array(
            np.s_[region.left:region.right],
            np.s_[region.top:region.bottom],
            c, z
        )

    def read_tile(self, tile, c=None, z=None, t=None):
        return self.read_window(
            tile, int(tile.width), int(tile.height), c, z, t
        )


class DicomSpatialConvertor(VipsSpatialConvertor):
    def vips_source(self):
        image = cached_dcmread(self.source).pixel_array
        return numpy_to_vips(to_unsigned_int(image))


class DicomFormat(AbstractFormat):
    """Dicom Format.

    References

    """
    checker_class = DicomChecker
    parser_class = DicomParser
    reader_class = DicomReader
    histogram_reader_class = DefaultHistogramReader  # TODO
    convertor_class = DicomSpatialConvertor

    def __init__(self, *args, **kwargs):
        super(DicomFormat, self).__init__(*args, **kwargs)
        self._enabled = True

    @classmethod
    def is_spatial(cls):
        return True

    @cached_property
    def need_conversion(self):
        imd = self.main_imd
        # TODO: the convertor only support 2D images
        return imd.width > 1024 or imd.height > 1024 and imd.depth == 1

    @property
    def media_type(self):
        return "application/dicom"
