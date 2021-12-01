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
from abc import ABC
from typing import Any, Callable, Dict, List, Tuple, Union

import numpy as np
import pyvips
from pyvips import Image as VIPSImage, Size as VIPSSize  # noqa
from rasterio.features import rasterize
from shapely.affinity import affine_transform
from shapely.geometry.base import BaseGeometry

from pims.api.utils.mimetype import OutputExtension
from pims.api.utils.models import ChannelReduction, Colorspace, PointCross
from pims.processing.adapters import (
    ImagePixels, ImagePixelsType, convert_to, imglib_adapters,
    numpy_to_vips
)
from pims.processing.annotations import (
    ParsedAnnotation, ParsedAnnotations, contour,
    stretch_contour
)
from pims.processing.colormaps import LookUpTable
from pims.utils.color import np_int2rgb
from pims.utils.iterables import find_first_available_int
from pims.utils.math import max_intensity
from pims.utils.vips import vips_dtype, vips_format_to_dtype

DEFAULT_WEBP_QUALITY = 75
DEFAULT_WEBP_LOSSLESS = False
DEFAULT_PNG_COMPRESSION = 6
DEFAULT_JPEG_QUALITY = 75

log = logging.getLogger("pims.processing")


class Op(ABC):
    """
    Base class for an operation.
    Operations are expected to be called like functions.
    """
    _impl: Dict[Any, Callable]

    def __init__(self):
        self._impl = {}

    @property
    def name(self):
        return self.__class__.__name__

    @property
    def parameters(self):
        return {
            k: v for (k, v) in self.__dict__.items()
            if not k.startswith("_")
        }

    @property
    def implementations(self) -> List[Any]:
        return list(self._impl.keys())

    def __call__(
        self, obj: Any, *args, **kwargs
    ) -> Any:
        return self._impl[type(obj)](obj, *args, **kwargs)


class ImageOp(Op):
    """
    Base class that all image operations derive from.

    Image operations are expected to be called like a function:
    `MyImgOp(param)(img)`.
    """
    _impl: Dict[ImagePixelsType, Callable]

    @property
    def implementations(self) -> List[ImagePixelsType]:
        return super(ImageOp, self).implementations

    @property
    def implementation_adapters(
        self
    ) -> Dict[Tuple[ImagePixelsType, ImagePixelsType], Callable]:
        return imglib_adapters

    def __call__(
        self, obj: ImagePixels, *args, **kwargs
    ) -> Union[ImagePixels, bytes]:
        """
        Apply image operation on given obj. Return type is a convertible
        image type (but not necessarily the type of `obj`).
        """
        # start = time.time()

        if type(obj) not in self.implementations:
            obj = self.implementation_adapters.get(
                (type(obj), self.implementations[0])
            )(obj)

        processed = self._impl[type(obj)](obj, *args, **kwargs)

        # end = time.time()
        # log.info(f"Apply {self.name} in {round((end - start) / 1e-6, 3)}µs")

        return processed


class OutputProcessor(ImageOp):
    """
    Compress image pixels to given format and return result in a byte buffer.
    """
    def __init__(self, format: OutputExtension, bitdepth: int, **format_params):
        super().__init__()
        self._impl[VIPSImage] = self._vips_impl
        self.format = format
        self.bitdepth = bitdepth
        self.format_params = format_params

    def _vips_impl(self, img: VIPSImage) -> bytes:
        suffix = self.format
        params = self.format_params
        clean_params = {}
        if suffix == OutputExtension.JPEG:
            clean_params['Q'] = params.get(
                'quality',
                params.get('jpeg_quality', DEFAULT_JPEG_QUALITY)
            )
            clean_params['strip'] = True
        elif suffix == OutputExtension.PNG:
            clean_params['compression'] = params.get(
                'compression',
                params.get('png_compression', DEFAULT_PNG_COMPRESSION)
            )
        elif suffix == OutputExtension.WEBP:
            clean_params['lossless'] = params.get(
                'lossless',
                params.get('webp_lossless', DEFAULT_WEBP_LOSSLESS)
            )
            clean_params['strip'] = True
            clean_params['Q'] = params.get(
                'quality',
                params.get('webp_quality', DEFAULT_WEBP_QUALITY)
            )

        # Clip by casting image
        img = img.cast(vips_dtype(self.bitdepth))
        return img.write_to_buffer(suffix, **clean_params)


class ResizeImgOp(ImageOp):
    """
    Resize a 2D image to expected size.
    Modifies: image width / image height
    Out shape: new_width * new_height * n_channels
    """

    def __init__(self, width: int, height: int):
        super().__init__()
        self._impl[VIPSImage] = self._vips_impl
        self.width = width
        self.height = height

    def _vips_impl(self, img: VIPSImage) -> VIPSImage:
        if img.width != self.width or img.height != self.height:
            img = img.thumbnail_image(
                self.width, height=self.height, size=VIPSSize.FORCE
            )
        return img


class ApplyLutImgOp(ImageOp):
    """
    Apply lookup table (LUT) on image pixels.
    Modifies: pixel intensities
    Out shape: width * height * n_channels
    """

    def __init__(self, lut: LookUpTable):
        super().__init__()
        self._impl[VIPSImage] = self._vips_impl
        self.lut = lut

    def _vips_impl(self, img: VIPSImage) -> VIPSImage:
        if self.lut is None:
            return img
        lut = self.lut[np.newaxis, :, :]
        return img.maplut(convert_to(lut, VIPSImage))


class ColorspaceImgOp(ImageOp):
    """
    Change colorspace of image pixels.
    Modifies: pixel intensities
    Out shape: width * height * n_channels
    """
    def __init__(self, colorspace: Colorspace):
        super().__init__()
        self._impl[VIPSImage] = self._vips_impl
        self.colorspace = colorspace

    def _vips_impl(self, img: VIPSImage) -> VIPSImage:
        new_colorspace = img.interpretation
        if self.colorspace == Colorspace.COLOR:
            new_colorspace = pyvips.enums.Interpretation.RGB
        elif self.colorspace == Colorspace.GRAY:
            new_colorspace = pyvips.enums.Interpretation.B_W

        if (img.interpretation == pyvips.enums.Interpretation.RGB16
                and self.colorspace == Colorspace.GRAY):
            new_colorspace = pyvips.enums.Interpretation.GREY16
        elif (img.interpretation == pyvips.enums.Interpretation.GREY16
              and self.colorspace == Colorspace.COLOR):
            new_colorspace = pyvips.enums.Interpretation.RGB16

        return img.colourspace(new_colorspace)


class ExtractChannelOp(ImageOp):
    """
    Extract channel at given index from an image.
    Modifies: number of channels
    Out shape: width * height * 1
    """
    def __init__(self, channel: int):
        super().__init__()
        self._impl[VIPSImage] = self._vips_impl
        self.channel = channel

    def _vips_impl(self, img: VIPSImage) -> VIPSImage:
        return img.extract_band(self.channel)


class ReductionOp(ImageOp):
    """
    Base class for reduction operations
    """
    def __call__(
        self, obj: List[ImagePixels], *args, **kwargs
    ) -> Union[ImagePixels, bytes]:
        if type(obj) is list and len(obj) > 0:
            type_obj = type(obj[0])
        else:
            type_obj = type(obj)

        if type_obj not in self.implementations:
            obj = self.implementation_adapters.get(
                (type_obj, self.implementations[0])
            )(obj)

        return self._impl[type_obj](obj, *args, **kwargs)


class ChannelReductionOp(ReductionOp):
    """
    Combine a list of ImagePixels into a single ImagePixels.
    All input image pixels must have same type and dimensions.
    """
    def __init__(self, reduction: ChannelReduction):
        super().__init__()
        self._impl[VIPSImage] = self._vips_impl
        self.reduction = reduction

    def _vips_impl(self, imgs: List[VIPSImage]) -> VIPSImage:
        if len(imgs) == 1:
            return imgs[0]
        format = imgs[0].format

        if self.reduction == ChannelReduction.AVG:
            reduction_operator = None  # TODO
            raise NotImplementedError
        elif self.reduction == ChannelReduction.MAX:
            reduction_operator = None  # TODO
            raise NotImplementedError
        elif self.reduction == ChannelReduction.MIN:
            reduction_operator = None  # TODO
            raise NotImplementedError
        else:
            reduction_operator = VIPSImage.sum

        return reduction_operator(imgs).cast(format)


class TransparencyMaskImgOp(ImageOp):
    """
    Add a transparency mask on image pixels
    Modifies: number of channels (add alpha)
    Out shape: width * height * (n_channels+1)
    """
    def __init__(self, bg_transparency: int, mask: np.ndarray, bitdepth: int):
        super(TransparencyMaskImgOp, self).__init__()
        self._impl[VIPSImage] = self._vips_impl
        self._impl[np.ndarray] = self._numpy_impl

        self.bg_transparency = bg_transparency
        self.mask = mask
        self.bitdepth = bitdepth

    def processed_mask(self, dtype: np.dtype) -> np.ndarray:
        mi = max_intensity(self.bitdepth)
        mask = self.mask.astype(dtype)
        mask[mask > 0] = 1 * mi
        mask[mask == 0] = (1 - self.bg_transparency / 100) * mi
        return mask

    def _vips_impl(self, img: VIPSImage) -> VIPSImage:
        mask = numpy_to_vips(
            self.processed_mask(vips_format_to_dtype[img.format])
        )
        return img.bandjoin(mask)

    def _numpy_impl(self, img: np.ndarray) -> np.ndarray:
        return np.dstack((img, self.processed_mask(img.dtype)))


class DrawOnImgOp(ImageOp):
    """
    Superpose a draw on image pixels.
    Modifies: pixel intensities
    Out shape: width * height * n_channels
    """
    def __init__(
        self, draw: np.ndarray, bitdepth: int, rgb_int_background: int = 0
    ):
        super(DrawOnImgOp, self).__init__()
        self._impl[VIPSImage] = self._vips_impl
        self._impl[np.ndarray] = self._numpy_impl

        self.draw = draw
        self.bitdepth = bitdepth
        self.rgb_int_background = rgb_int_background

    def _vips_impl(self, img: VIPSImage) -> VIPSImage:
        draw = numpy_to_vips(
            self.processed_draw(vips_format_to_dtype[img.format])
        )
        cond = numpy_to_vips(self.condition_mask())
        return cond.ifthenelse(img, draw)

    def _numpy_impl(self, img: np.ndarray) -> np.ndarray:
        draw = np.atleast_3d(self.processed_draw(img.dtype))
        cond = np.atleast_3d(self.condition_mask())
        return np.where(cond, img, draw)

    def processed_draw(self, dtype: np.dtype) -> np.ndarray:
        draw = self.draw
        if self.bitdepth > 8:
            draw = draw.astype(np.float)
            draw /= 255
            draw *= max_intensity(self.bitdepth)

        draw = draw.astype(dtype)
        return draw

    def condition_mask(self) -> np.ndarray:
        """
        True -> image
        False -> drawing
        """
        if self.draw.ndim == 3:
            bg = np_int2rgb(self.rgb_int_background)
            return np.all(self.draw == np.asarray(bg), axis=-1).astype(np.uint8)
        else:
            mask = np.ones_like(self.draw, dtype=np.uint8)
            mask[self.draw != self.rgb_int_background] = 0
            return mask


class RasterOp(Op):
    """
    Base class for rasterization operations
    """
    _impl = Dict[ParsedAnnotations, Callable]

    def __call__(
        self, obj: ParsedAnnotations, *args, **kwargs
    ) -> Union[ImagePixels, bytes]:
        return super().__call__(obj, *args, **kwargs)


class MaskRasterOp(RasterOp):
    """
    Rasterize annotations to a mask.
    """
    def __init__(self, affine: np.ndarray, out_width: int, out_height: int):
        super().__init__()
        self._impl[ParsedAnnotations] = self._default_impl

        self.affine_matrix = affine
        self.out_width = out_width
        self.out_height = out_height

    def _to_shape(
        self, annot: ParsedAnnotation, is_grayscale: bool = True
    ) -> Tuple[BaseGeometry, int]:
        geometry = affine_transform(annot.geometry, self.affine_matrix)
        if is_grayscale:
            value = annot.fill_color.as_rgb_tuple()[0]
        else:
            value = annot.fill_color.as_int()
        return geometry, value

    def _default_impl(self, annots: ParsedAnnotations) -> np.ndarray:
        out_shape = (self.out_height, self.out_width)
        dtype = np.uint8 if annots.is_fill_grayscale else np.uint32

        def shape_generator():
            for annot in annots:
                yield self._to_shape(annot, annots.is_fill_grayscale)

        rasterized = rasterize(
            shape_generator(), out_shape=out_shape, dtype=dtype
        )
        if not annots.is_grayscale:
            return np_int2rgb(rasterized)
        return rasterized


class DrawRasterOp(RasterOp):
    """
    Rasterize annotations contours.
    """
    def __init__(
        self, affine: np.ndarray, out_width: int, out_height: int,
        point_style: PointCross
    ):
        super().__init__()
        self._impl[ParsedAnnotations] = self._default_impl

        self.affine_matrix = affine
        self.out_width = out_width
        self.out_height = out_height
        self.point_style = point_style

    @staticmethod
    def _contour_width(stroke_width: int, out_shape: Tuple[int, int]) -> int:
        return round(stroke_width * (0.75 + max(out_shape) / 1000))

    def _to_shape(
        self, annot: ParsedAnnotation, out_shape: Tuple[int, int],
        is_grayscale: bool = True
    ) -> Tuple[BaseGeometry, int]:
        width = self._contour_width(annot.stroke_width, out_shape)
        geometry = stretch_contour(
            affine_transform(
                contour(annot.geometry, point_style=self.point_style),
                self.affine_matrix
            ), width=width
        )
        value = annot.stroke_color.as_rgb_tuple()[
            0] if is_grayscale else annot.stroke_color.as_int()
        return geometry, value

    def _default_impl(self, annots: ParsedAnnotations) -> np.ndarray:
        out_shape = (self.out_height, self.out_width)
        dtype = np.uint8 if annots.is_stroke_grayscale else np.uint32

        def shape_generator():
            for annot in annots:
                if not annot.stroke_color:
                    continue
                yield self._to_shape(
                    annot, out_shape, annots.is_stroke_grayscale
                )

        bg = self.background_color(annots)
        try:
            rasterized = rasterize(
                shape_generator(), out_shape=out_shape, dtype=dtype, fill=bg
            )
        except ValueError:
            # No valid geometry objects found for rasterize
            rasterized = np.full(out_shape, bg)
        if not annots.is_grayscale:
            return np_int2rgb(rasterized)
        return rasterized

    @staticmethod
    def background_color(annots: ParsedAnnotations) -> int:
        """
        Find an integer to use for background (cannot be 0 if one of stroke
        color is black).
        """
        if annots.is_stroke_grayscale:
            values = [
                a.stroke_color.as_rgb_tuple()[0]
                for a in annots if a.stroke_color
            ]
            return find_first_available_int(values, 0, 65536)
        else:
            values = [a.stroke_color.as_int() for a in annots if a.stroke_color]
            return find_first_available_int(values, 0, 4294967296)


class HistOp(Op):
    """
    Base class for histogram operations
    """
    _impl = Dict[np.ndarray, Callable]

    def __call__(
        self, obj: np.ndarray, *args, **kwargs
    ) -> np.ndarray:
        return super().__call__(obj, *args, **kwargs)


class RescaleHistOp(HistOp):
    """
    Rescale an histogram for a given bit depth. It is used to rescale values
    so that out = in * (pow(2, bitdepth) - 1)
    """

    def __init__(self, bitdepth: int):
        super().__init__()
        self._impl[np.ndarray] = self._numpy_impl
        self.bitdepth = bitdepth

    def _numpy_impl(self, hist: np.ndarray) -> np.ndarray:
        return hist.reshape(
            (hist.shape[0], max_intensity(self.bitdepth, count=True), -1)
        ).sum(axis=2)


class ColorspaceHistOp(HistOp):
    """
    Transform histogram colorspace if needed.
    """
    def __init__(self, colorspace: Colorspace):
        super().__init__()
        self._impl[np.ndarray] = self._numpy_impl
        self.colorspace = colorspace

    def _numpy_impl(self, hist: np.ndarray) -> np.ndarray:
        hist = np.transpose(hist)
        n_channels = hist.shape[-1]

        if self.colorspace == Colorspace.GRAY and n_channels != 1:
            n_used_channels = min(n_channels, 3)
            luminance = [0.2125, 0.7154, 0.0721]
            return hist[:n_used_channels] \
                @ np.array(luminance[:n_used_channels])
        elif self.colorspace == Colorspace.COLOR and n_channels != 3:
            return np.dstack((hist, hist, hist))
        else:
            return hist
