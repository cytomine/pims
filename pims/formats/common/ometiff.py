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
from functools import cached_property
from typing import Optional

import numpy as np
from pint import Quantity
from pyvips import Image as VIPSImage
from tifffile import TiffFile, TiffPageSeries, xml2dict

from pims.formats import AbstractFormat
from pims.formats.utils.abstract import CachedDataPath
from pims.formats.utils.engines.omexml import OMEXML
from pims.formats.utils.engines.tifffile import TifffileChecker, TifffileParser, cached_tifffile
from pims.formats.utils.engines.vips import VipsReader
from pims.formats.utils.histogram import DefaultHistogramReader
from pims.formats.utils.structures.metadata import ImageChannel, ImageMetadata, MetadataStore
from pims.formats.utils.structures.planes import PlanesInfo
from pims.formats.utils.structures.pyramid import Pyramid
from pims.utils import UNIT_REGISTRY
from pims.utils.color import infer_channel_color
from pims.utils.dict import flatten
from pims.utils.dtypes import dtype_to_bits


def clean_ome_dict(d: dict) -> dict:
    for k, v in d.items():
        if k.endswith('Settings') or k.endswith('Ref'):
            continue

        if type(v) is dict:
            if 'ID' in v.keys():
                id = ''.join([f"[{i}]" for i in v['ID'].split(':')[1:]])
                del v['ID']
                v = {id: v}
                d[k] = v
            d[k] = clean_ome_dict(v)
        elif type(v) is list:
            new_v = dict()
            for item in v:
                if 'ID' in item.keys():
                    id = ''.join([f"[{i}]" for i in item['ID'].split(':')[1:]])
                    del item['ID']
                    new_v[id] = item
            if len(new_v) == 0:
                new_v = v
            d[k] = new_v

    # TODO: original metadata from StructuredAnnotations
    return d


def parse_ome(omexml: str) -> OMEXML:
    return OMEXML(omexml)


def cached_omexml(format: AbstractFormat) -> OMEXML:
    tf = cached_tifffile(format)
    return format.get_cached('_omexml', parse_ome, tf.pages[0].description)


def cached_omedict(format: AbstractFormat) -> dict:
    tf = cached_tifffile(format)
    return format.get_cached('_omedict', xml2dict, tf.pages[0].description)


def cached_tifffile_baseseries(format: AbstractFormat) -> TiffPageSeries:
    tf = cached_tifffile(format)

    def get_baseseries(tf: TiffFile) -> TiffPageSeries:
        idx = np.argmax([np.prod(s.shape) for s in tf.series])
        return tf.series[idx]

    return format.get_cached('_tf_baseseries', get_baseseries, tf)


class OmeTiffChecker(TifffileChecker):
    @classmethod
    def match(cls, pathlike: CachedDataPath) -> bool:
        if super().match(pathlike):
            tf = cls.get_tifffile(pathlike)
            return tf.is_ome
        return False


class OmeTiffParser(TifffileParser):
    @property
    def base(self) -> TiffPageSeries:
        return cached_tifffile_baseseries(self.format)

    def parse_main_metadata(self) -> ImageMetadata:
        base = self.base
        shape = dict(zip(base.axes, base.shape))

        imd = ImageMetadata()
        imd.width = shape['X']
        imd.height = shape['Y']
        imd.depth = shape.get('Z', 1)
        imd.duration = shape.get('T', 1)

        imd.pixel_type = base.dtype
        imd.significant_bits = dtype_to_bits(imd.pixel_type)

        imd.n_channels = shape.get('C', 1) * shape.get('S', 1)
        imd.n_intrinsic_channels = shape.get('C', 1)
        imd.n_channels_per_read = shape.get('S', 1)

        omexml = cached_omexml(self.format)
        base = omexml.main_image

        if imd.n_channels == 3:
            default_names = ['R', 'G', 'B']
        elif imd.n_channels == 2:
            default_names = ['R', 'G']
        elif imd.n_channels == 1:
            default_names = ['L']
        else:
            default_names = None

        for c in range(imd.n_channels):
            ome_c = (c - (c % imd.n_channels_per_read)) // imd.n_channels_per_read
            channel = base.pixels.channel(ome_c)
            name = channel.name
            if not name and default_names is not None:
                name = default_names[c]
            color = infer_channel_color(channel.color, c, imd.n_channels)
            imd.set_channel(
                ImageChannel(
                    index=c, emission_wavelength=channel.emission_wavelength,
                    excitation_wavelength=channel.excitation_wavelength,
                    suggested_name=name,
                    color=color
                )
            )

        return imd

    def parse_known_metadata(self) -> ImageMetadata:
        omexml = cached_omexml(self.format)
        base = omexml.main_image

        imd = super().parse_known_metadata()
        imd.description = base.description
        imd.acquisition_datetime = self.parse_ome_acquisition_date(
            base.acquisition_date
        )

        imd.physical_size_x = self.parse_ome_physical_size(
            base.pixels.physical_size_X, base.pixels.physical_size_X_unit
        )
        imd.physical_size_y = self.parse_ome_physical_size(
            base.pixels.physical_size_Y, base.pixels.physical_size_Y_unit
        )
        imd.physical_size_z = self.parse_ome_physical_size(
            base.pixels.physical_size_Z, base.pixels.physical_size_Z_unit
        )
        imd.frame_rate = self.parse_frame_rate(
            base.pixels.time_increment, base.pixels.time_increment_unit
        )

        if base.instrument is not None and \
                base.instrument.microscope is not None:
            imd.microscope.model = base.instrument.microscope.model

        if base.objective is not None:
            imd.objective.nominal_magnification = \
                base.objective.nominal_magnification
            imd.objective.calibrated_magnification = \
                base.objective.calibrated_magnification

        for i in range(omexml.image_count):
            base = omexml.image(i)
            name = base.name.lower() if base.name else None
            if name == "thumbnail":
                associated = imd.associated_thumb
            elif name == "label":
                associated = imd.associated_label
            elif name == "macro":
                associated = imd.associated_macro
            else:
                continue
            associated.width = base.pixels.size_X
            associated.height = base.pixels.size_Y
            associated.n_channels = base.pixels.size_C

        imd.is_complete = True
        return imd

    @staticmethod
    def parse_frame_rate(
        time_increment: Optional[float], unit: Optional[str]
    ) -> Optional[Quantity]:
        if unit is None:
            unit = 's'
        if time_increment in [None, 0]:
            return None
        return 1 / time_increment * UNIT_REGISTRY(unit)

    @staticmethod
    def parse_ome_physical_size(
        physical_size: Optional[float], unit: Optional[str]
    ) -> Optional[Quantity]:
        if unit is None:
            unit = 'µm'
        if physical_size in [None, 0] or unit in ['pixel', 'reference frame']:
            return None
        return physical_size * UNIT_REGISTRY(unit)

    @staticmethod
    def parse_ome_acquisition_date(date: Optional[str]) -> Optional[datetime]:
        if date is None:
            return None
        return datetime.fromisoformat(date)

    def parse_raw_metadata(self) -> MetadataStore:
        store = super().parse_raw_metadata()
        ome = flatten(clean_ome_dict(cached_omedict(self.format)))
        for full_key, value in ome.items():
            key = full_key.split('.')[-1]
            if key not in ('TiffData', 'BinData'):
                store.set(full_key, value)

        return store

    def parse_pyramid(self) -> Pyramid:
        base_series = cached_tifffile_baseseries(self.format)

        pyramid = Pyramid()
        for i, level in enumerate(base_series.levels):
            page = level[0]
            tilewidth = page.tilewidth if page.tilewidth != 0 else page.imagewidth
            tilelength = page.tilelength if page.tilelength != 0 else page.imagelength
            subifd = i - 1 if i > 0 else None
            pyramid.insert_tier(
                page.imagewidth, page.imagelength,
                (tilewidth, tilelength),
                subifd=subifd
            )

        return pyramid

    def parse_planes(self) -> PlanesInfo:
        omexml = cached_omexml(self.format)
        base = omexml.main_image

        imd = self.format.main_imd
        pi = PlanesInfo(
            imd.n_intrinsic_channels, imd.depth, imd.duration,
            ['page_index'], [np.int]
        )

        for i in range(base.pixels.tiff_data_count):
            td = base.pixels.tiff_data(i)
            pi.set(td.first_c, td.first_z, td.first_t, page_index=td.ifd)

        return pi


class OmeTiffReader(VipsReader):
    def read_thumb(self, out_width, out_height, precomputed=None, c=None, z=None, t=None):
        # TODO: precomputed ?
        # Thumbnail already uses shrink-on-load feature in default VipsReader
        # (i.e it loads the right pyramid level according the requested dimensions)
        page = self.format.planes_info.get(c, z, t, 'page_index')
        im = self.vips_thumbnail(out_width, out_height, page=page)
        return im.flatten() if im.hasalpha() else im

    def read_window(self, region, out_width, out_height, c=None, z=None, t=None):
        tier = self.format.pyramid.most_appropriate_tier(
            region, (out_width, out_height)
        )
        region = region.scale_to_tier(tier)

        page = self.format.planes_info.get(c, z, t, 'page_index')
        subifd = tier.data.get('subifd')
        opts = dict(page=page)
        if subifd is not None:
            opts['subifd'] = subifd
        tiff_page = VIPSImage.tiffload(str(self.format.path), **opts)
        return tiff_page.extract_area(
            region.left, region.top, region.width, region.height
        )

    def read_tile(self, tile, c=None, z=None, t=None):
        tier = tile.tier
        page = self.format.planes_info.get(c, z, t, 'page_index')
        subifd = tier.data.get('subifd')
        opts = dict(page=page)
        if subifd is not None:
            opts['subifd'] = subifd
        tiff_page = VIPSImage.tiffload(str(self.format.path), **opts)
        return tiff_page.extract_area(
            tile.left, tile.top, tile.width, tile.height
        )


class OmeTiffFormat(AbstractFormat):
    """
    OME-TIFF format.

    Known limitations:
    *

    References:

    """
    checker_class = OmeTiffChecker
    parser_class = OmeTiffParser
    reader_class = OmeTiffReader
    histogram_reader_class = DefaultHistogramReader

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._enabled = True

    @classmethod
    def get_name(cls):
        return "OME-TIFF"

    @classmethod
    def is_spatial(cls):
        return True

    @cached_property
    def need_conversion(self):
        return False

    @property
    def media_type(self):
        return "ome/ome-tiff"
