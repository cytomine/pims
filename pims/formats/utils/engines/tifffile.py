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
from datetime import datetime
from enum import Enum
from typing import Any, Optional, Tuple, Union

import numpy as np
from pint import Quantity
from tifffile import TiffPage, TiffTag, tifffile

from pims.formats import AbstractFormat
from pims.formats.utils.abstract import CachedDataPath
from pims.formats.utils.checker import SignatureChecker
from pims.formats.utils.parser import AbstractParser
from pims.formats.utils.structures.metadata import ImageChannel, ImageMetadata, MetadataStore
from pims.formats.utils.structures.pyramid import Pyramid
from pims.utils import UNIT_REGISTRY
from pims.utils.types import parse_datetime

TIFF_FLAGS = (
    'geotiff',
    'philips',
    'shaped',
    'lsm',
    'ome',
    'imagej',
    'fluoview',
    'stk',
    'sis',
    'svs',
    'scn',
    'qpi',
    'ndpi',
    'scanimage',
    'mdgel',
)


def read_tifffile(path, silent_fail=True):
    try:
        tf = tifffile.TiffFile(path)
    except tifffile.TiffFileError as error:
        if not silent_fail:
            raise error
        tf = None
    return tf


def cached_tifffile(
    format: Union[AbstractFormat, CachedDataPath]
) -> tifffile.TiffFile:
    return format.get_cached(
        '_tf', read_tifffile, format.path.resolve(), silent_fail=True
    )


def cached_tifffile_baseline(format: AbstractFormat) -> TiffPage:
    tf = cached_tifffile(format)
    return format.get_cached('_tf_baseline', tf.pages.__getitem__, 0)


def get_tifftag_value(tag: Union[TiffTag, Any]) -> Any:
    if isinstance(tag, TiffTag):
        return tag.value
    else:
        return tag


class TifffileChecker(SignatureChecker):
    @classmethod
    def get_tifffile(cls, pathlike: CachedDataPath):
        return cached_tifffile(pathlike)

    @classmethod
    def match(cls, pathlike: CachedDataPath) -> bool:
        buf = cls.get_signature(pathlike)
        if not (len(buf) > 2 and (
                buf[0] == buf[1] == 0x49 or
                buf[0] == buf[1] == 0x4D)):
            return False

        return cls.get_tifffile(pathlike) is not None


class TifffileParser(AbstractParser):
    @property
    def baseline(self) -> TiffPage:
        return cached_tifffile_baseline(self.format)

    def parse_main_metadata(self) -> ImageMetadata:
        baseline = self.baseline

        imd = ImageMetadata()
        imd.width = baseline.imagewidth
        imd.height = baseline.imagelength
        imd.depth = baseline.imagedepth
        imd.duration = 1
        imd.n_intrinsic_channels = 1

        imd.pixel_type = baseline.dtype
        imd.significant_bits = baseline.bitspersample

        imd.n_channels = baseline.samplesperpixel
        if imd.n_channels == 3:
            imd.set_channel(ImageChannel(index=0, suggested_name='R'))
            imd.set_channel(ImageChannel(index=1, suggested_name='G'))
            imd.set_channel(ImageChannel(index=2, suggested_name='B'))
        else:
            imd.set_channel(ImageChannel(index=0, suggested_name='L'))
        imd.n_channels_per_read = imd.n_channels

        return imd

    def parse_known_metadata(self) -> ImageMetadata:
        imd = super().parse_known_metadata()
        baseline = self.baseline
        tags = baseline.tags

        imd.description = baseline.description
        imd.acquisition_datetime = self.parse_acquisition_date(tags.get(306))

        imd.physical_size_x = self.parse_physical_size(
            tags.get("XResolution"), tags.get("ResolutionUnit")
        )
        imd.physical_size_y = self.parse_physical_size(
            tags.get("YResolution"), tags.get("ResolutionUnit")
        )
        return imd

    @staticmethod
    def parse_acquisition_date(
        date: Union[datetime, str, TiffTag]
    ) -> Union[datetime, None]:
        """
        Parse a date(time) from a TiffTag to datetime.

        Parameters
        ----------
        date: str, datetime, TiffTag

        Returns
        -------
        datetime: datetime, None
        """
        date = get_tifftag_value(date)

        if isinstance(date, datetime):
            return date
        elif not isinstance(date, str) or (len(date) != 19 or date[16] != ':'):
            return None
        else:
            return parse_datetime(date, raise_exc=False)

    @staticmethod
    def parse_physical_size(
        physical_size: Union[Tuple, float, TiffTag],
        unit: Optional[tifffile.TIFF.RESUNIT] = None
    ) -> Union[Quantity, None]:
        """
        Parse a physical size and its unit from a TiffTag to a Quantity.
        """
        physical_size = get_tifftag_value(physical_size)
        unit = get_tifftag_value(unit)
        if not unit or physical_size is None:
            return None
        if type(physical_size) == tuple and len(physical_size) == 1:
            rational = (physical_size[0], 1)
        elif type(physical_size) != tuple:
            rational = (physical_size, 1)
        else:
            rational = physical_size
        if rational[0] == 0:
            return None
        return rational[1] / rational[0] * UNIT_REGISTRY(unit.name.lower())

    def parse_raw_metadata(self) -> MetadataStore:
        baseline = cached_tifffile_baseline(self.format)
        store = super().parse_raw_metadata()

        # Tags known to be not parsable, unnecessary or hazardous.
        skipped_tags = (273, 279, 278, 288, 289, 320, 324, 325,
                        347, 437, 519, 520, 521, 559, 20624,
                        20625, 34675) + tuple(range(65420, 65459))

        for tag in baseline.tags:
            if tag.code not in skipped_tags and \
                    type(tag.value) not in (bytes, np.ndarray):
                if isinstance(tag.value, Enum):
                    value = tag.value.name
                else:
                    value = tag.value
                store.set(tag.name, value, namespace="TIFF")
        return store

    def parse_pyramid(self) -> Pyramid:
        image = cached_tifffile(self.format)
        base_series = image.series[0]

        pyramid = Pyramid()
        for level in base_series.levels:
            page = level[0]
            pyramid.insert_tier(
                page.imagewidth, page.imagelength,
                (page.tilewidth, page.tilelength),
                page_index=page.index
            )

        return pyramid
