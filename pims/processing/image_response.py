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
from abc import ABC, abstractmethod
from functools import lru_cache
from typing import Dict, List, Optional, Tuple, Type

import numpy as np
from starlette.responses import Response

from pims.api.utils.mimetype import OutputExtension
from pims.api.utils.models import (
    AnnotationStyleMode, AssociatedName, ChannelReduction,
    Colorspace, GenericReduction, PointCross
)
from pims.files.image import Image
from pims.filters import AbstractFilter
from pims.processing.adapters import ImagePixels
from pims.processing.annotations import ParsedAnnotations
from pims.processing.colormaps import (
    Colormap, StackedLookUpTables, combine_stacked_lut, default_lut,
    get_lut_from_stacked, is_rgb_colormapping
)
from pims.processing.operations import (
    ApplyLutImgOp, ChannelReductionOp, ColorspaceHistOp,
    ColorspaceImgOp, DrawOnImgOp, DrawRasterOp, ExtractChannelOp,
    MaskRasterOp, OutputProcessor,
    RescaleHistOp, ResizeImgOp, TransparencyMaskImgOp
)
from pims.processing.region import Region, Tile
from pims.utils.dtypes import np_dtype
from pims.utils.math import max_intensity


class ImageResponse(ABC):
    """
    Base class for an image response.
    """

    def __init__(
        self, in_image: Optional[Image], out_format: OutputExtension,
        out_width: int, out_height: int, out_bitdepth: int = 8, **kwargs
    ):
        self.in_image = in_image

        self.out_width = out_width
        self.out_height = out_height
        self.out_format = out_format
        self.out_bitdepth = out_bitdepth
        self.out_format_params = {
            k.replace('out_format_', ''): v
            for k, v in kwargs.items() if k.startswith('out_format_')
        }

    @property
    def best_effort_bitdepth(self) -> int:
        """Depending on output format, asked bitdepth could be downgraded."""
        if self.out_format == OutputExtension.PNG:
            return min(self.out_bitdepth, 16)
        return min(self.out_bitdepth, 8)

    @property
    def max_intensity(self):
        return max_intensity(self.best_effort_bitdepth)

    @abstractmethod
    def process(self) -> ImagePixels:
        """
        Process the image pixels according to in/out parameters.
        """
        pass

    def get_response_buffer(self) -> bytes:
        """
        Get image response compressed using output extension compressor,
        in bytes.
        """
        return OutputProcessor(
            self.out_format, self.best_effort_bitdepth,
            **self.out_format_params
        )(self.process())

    def http_response(
        self, mimetype: str, extra_headers: Optional[Dict[str, str]] = None
    ) -> Response:
        """
        Encapsulate image response into an HTTP response, ready to be sent to
        the client.
        """
        return Response(
            content=self.get_response_buffer(),
            headers=extra_headers,
            media_type=mimetype
        )


class MultidimImageResponse(ImageResponse, ABC):
    """
    Base class for multidimensional image response.
    """

    def __init__(
        self, in_image: Image,
        in_channels: List[int], in_z_slices: List[int], in_timepoints: List[int],
        out_format: OutputExtension, out_width: int, out_height: int,
        out_bitdepth: int, c_reduction: ChannelReduction,
        z_reduction: GenericReduction, t_reduction: GenericReduction, **kwargs
    ):
        super().__init__(
            in_image, out_format, out_width, out_height, out_bitdepth, **kwargs
        )
        self.in_image = in_image
        self.channels = in_channels
        self.z_slices = in_z_slices
        self.timepoints = in_timepoints

        self.c_reduction = c_reduction
        self.z_reduction = z_reduction
        self.t_reduction = t_reduction

    def raw_view_planes(self) -> Tuple[List[int], int, int]:
        # TODO: generalize
        # PIMS API currently only allow requests for 1 Z or T plane and 1 or all C planes
        return self.channels, self.z_slices[0], self.timepoints[0]

    @abstractmethod
    def raw_view(self, c: int, z: int, t: int) -> ImagePixels:
        # TODO
        pass


class ProcessedView(MultidimImageResponse, ABC):
    """
    Base class for image responses with processing.
    """
    def __init__(
        self, in_image: Image,
        in_channels: List[int], in_z_slices: List[int], in_timepoints: List[int],
        out_format: OutputExtension, out_width: int, out_height: int,
        out_bitdepth: int, c_reduction: ChannelReduction,
        z_reduction: GenericReduction, t_reduction: GenericReduction,
        gammas: List[float], filters: List[Type[AbstractFilter]],
        colormaps: List[Colormap], min_intensities: List[int],
        max_intensities: List[int], log: bool,
        colorspace: Colorspace = Colorspace.AUTO, **kwargs
    ):
        super().__init__(
            in_image, in_channels, in_z_slices, in_timepoints,
            out_format, out_width, out_height, out_bitdepth,
            c_reduction, z_reduction, t_reduction, **kwargs
        )

        self.gammas = gammas
        self.filters = filters
        self.colormaps = colormaps
        self.min_intensities = min_intensities
        self.max_intensities = max_intensities
        self.log = log
        self.colorspace = colorspace

    @property
    def gamma_processing(self) -> bool:
        """Whether gamma processing is required"""
        return any(gamma != 1.0 for gamma in self.gammas)

    @property
    def log_processing(self) -> bool:
        """Whether log processing is required"""
        return self.log

    @property
    def intensity_processing(self) -> bool:
        """Whether intensity processing is required"""
        return (any(self.min_intensities)
                or any(i != self.max_intensity for i in self.max_intensities))

    @property
    def math_processing(self) -> bool:
        """Whether math lookup table has to be computed."""
        return (self.intensity_processing or
                self.gamma_processing or
                self.log_processing)

    @lru_cache(maxsize=None)
    def math_lut(self) -> Optional[StackedLookUpTables]:
        """
        Compute lookup table for math processing operations if any.

        Returns
        -------
        lut
            Stacked LUTs (n_channels, 2**img.bitdepth, 1)
        """
        if not self.math_processing:
            return None

        n_channels = len(self.channels)
        lut = np.zeros((n_channels, self.in_image.max_value + 1, 1))
        if self.intensity_processing:
            for c in range(n_channels):
                mini = self.min_intensities[c]
                maxi = self.max_intensities[c]
                diff = maxi - mini
                lut[c, mini:maxi] = np.linspace((0,), (1,), num=diff)
                lut[c, maxi:] = 1
        else:
            lut[:, :, 0] = np.linspace(
                (0,) * n_channels, (1,) * n_channels,
                num=self.in_image.max_value + 1
            ).T

        if self.gamma_processing:
            lut = np.power(lut, self.gammas)

        if self.log_processing:
            # Apply logarithmic scale on image.
            # Formula: out = ln(1+ in) * max_per_channel / ln(1 + max_per_channel)
            # Reference: Icy Logarithmic 2D viewer plugin
            # (http://icy.bioimageanalysis.org/plugin/logarithmic-2d-viewer/)
            lut = np.log1p(lut) * 1. / np.log1p(1)

        lut *= self.max_intensity
        return lut.astype(np_dtype(self.best_effort_bitdepth))

    @property
    def colormap_processing(self) -> bool:
        """Whether colormapping processing is required."""
        return any(self.colormaps)

    @lru_cache(maxsize=None)
    def colormap_lut(self) -> Optional[StackedLookUpTables]:
        """
        Compute lookup table from colormaps if any.

        Returns
        -------
        lut
            Array of shape (n_channels, 2**img.bitdepth, n_components)
        """
        if not self.colormap_processing:
            return None

        n_components = np.max(
            [colormap.n_components() if colormap else 1
             for colormap in self.colormaps]
        )
        return np.stack(
            [
                colormap.lut(
                    size=self.max_intensity + 1,
                    bitdepth=self.best_effort_bitdepth,
                    n_components=n_components
                ) if colormap else default_lut(
                    size=self.max_intensity + 1,
                    bitdepth=self.best_effort_bitdepth,
                    n_components=n_components
                ) for colormap in self.colormaps
            ]
        )

    @lru_cache(maxsize=None)
    def lut(self) -> Optional[StackedLookUpTables]:
        """
        The lookup table to apply combining all processing operations.
        """
        math_lut = self.math_lut()
        colormap_lut = self.colormap_lut()

        if math_lut is None:
            if colormap_lut is None:
                return None
            else:
                return colormap_lut
        else:
            if colormap_lut is None:
                return math_lut
            else:
                return combine_stacked_lut(math_lut, colormap_lut)

    # Colorspace

    @property
    def new_colorspace(self) -> Colorspace:
        """
        The colorspace for the image response if colorspace processing is
        required.
        """
        if self.colorspace == Colorspace.AUTO:
            if len(self.channels) == 1:
                colorspace = Colorspace.GRAY
            else:
                colorspace = Colorspace.COLOR
            return colorspace
        return self.colorspace

    @property
    def colorspace_processing(self) -> bool:
        """Whether colorspace needs to be changed."""
        if self.colorspace == Colorspace.AUTO:
            return False
        return (self.colorspace == Colorspace.GRAY and
                len(self.channels) > 1) or \
               (self.colorspace == Colorspace.COLOR and
                len(self.channels) == 1)

    # Filtering

    @property
    def filter_processing(self) -> bool:
        """Whether filters have to be applied."""
        return bool(len(self.filters))

    @property
    def filter_processing_histogram(self) -> bool:
        """If filtering, whether some filters require histograms."""
        return any([f.require_histogram() for f in self.filters])

    @property
    def filter_required_colorspace(self) -> Optional[Colorspace]:
        """
        If filtering and some filters require a specific colorspace, get the
        minimum satisfying colorspace.
        """
        colorspaces = [f.required_colorspace() for f in self.filters]
        if Colorspace.GRAY in colorspaces:
            return Colorspace.GRAY
        if Colorspace.COLOR in colorspaces:
            return Colorspace.COLOR
        return None

    @property
    def filter_colorspace_processing(self):
        """Whether colorspace needs to be changed before applying filters"""
        if self.filter_required_colorspace is None:
            return False
        return (self.filter_required_colorspace == Colorspace.GRAY and
                len(self.channels) > 1) or \
               (self.filter_required_colorspace == Colorspace.COLOR and
                len(self.channels) == 1)

    @property
    def filter_colorspace(self):
        """If needed, the required colorspace before applying filters"""
        if self.filter_required_colorspace is None:
            return self.colorspace
        return self.filter_required_colorspace

    def process(self) -> ImagePixels:
        # -- TODO: refactor/rewrite/optimize
        def channels_by_read(read_idx, in_image):
            """Get channel indexes returned by a given read."""
            first = read_idx * in_image.n_channels_per_read
            last = min(in_image.n_channels, first + in_image.n_channels_per_read)
            return range(first, last)

        response_channels, z, t = self.raw_view_planes()
        n_channels_per_read = self.in_image.n_channels_per_read

        reads = dict()  # Response channels acquired by given read
        for response_channel in response_channels:
            read = response_channel // self.in_image.n_channels_per_read
            if read in reads:
                reads[read].append(response_channel)
            else:
                reads[read] = [response_channel]

        # List[Tuple[ImagePixels, Union[int, Tuple[int, int, int]]]]
        response_channel_images = list()
        c_idx = 0
        for read, needed in reads.items():
            read_channels = channels_by_read(read, self.in_image)

            channel_image = self.raw_view(read_channels[0], z, t)  # TODO

            if len(read_channels) == 3:
                idxs = (c_idx, c_idx + 1, c_idx + 2)
                if (needed == [0, 1, 2] and is_rgb_colormapping(
                        [self.colormaps[idx] for idx in idxs]
                        )):
                    # RGB image, and no tinting required
                    c_idx = idxs[-1]
                    response_channel_images.append((channel_image, idxs))
                else:
                    # If len(needed) = 3 and RGB image, but channels need tinting
                    # If len(needed) = 1 and RGB image, but we want a single channel
                    for needed_channel in needed:
                        channel_idx = needed_channel % n_channels_per_read
                        image = ExtractChannelOp(channel_idx)(channel_image)
                        response_channel_images.append((image, c_idx))
                        c_idx += 1
            else:
                response_channel_images.append((channel_image, c_idx))
                c_idx += 1

        # List[ImagePixels]
        processed_channel_images = list()
        for img, channel in response_channel_images:
            if type(channel) is tuple:
                img = ApplyLutImgOp(
                    get_lut_from_stacked(self.math_lut())
                )(img)
            else:
                img = ApplyLutImgOp(
                    get_lut_from_stacked(self.lut(), channel)
                )(img)

            processed_channel_images.append(img)
        # ----------- end to optimize

        img = ChannelReductionOp(self.c_reduction)(processed_channel_images)
        img = ResizeImgOp(self.out_width, self.out_height)(img)

        if self.filter_processing:
            if self.filter_colorspace is not None:
                img = ColorspaceImgOp(self.filter_colorspace)(img)

            filter_params = dict()
            if self.filter_processing_histogram:
                filter_params['histogram'] = self.process_histogram()
            for filter_op in self.filters:
                img = filter_op(**filter_params)(img)

        if self.colorspace_processing:
            img = ColorspaceImgOp(self.new_colorspace)(img)
        return img

    def process_histogram(self) -> np.ndarray:
        """
        Process image histogram from in/out parameters, so that if can be
        used by histogram filters on processed images.
        """
        hist = self.in_image.histogram.plane_histogram(*self.raw_view_planes())
        hist = hist.squeeze()
        hist = hist.reshape((1, -1)) if hist.ndim == 1 else hist

        # TODO: filters are computed on best_effort bitdepth
        #  while it should do on image bitdepth
        hist = RescaleHistOp(self.best_effort_bitdepth)(hist)

        if self.filter_colorspace_processing:
            hist = ColorspaceHistOp(self.filter_colorspace)(hist)
        return hist.squeeze()


class ThumbnailResponse(ProcessedView):
    def __init__(
        self, in_image: Image, in_channels: List[int], in_z_slices: List[int],
        in_timepoints: List[int], out_format: OutputExtension, out_width: int,
        out_height: int, c_reduction: ChannelReduction, z_reduction: GenericReduction,
        t_reduction: GenericReduction, gammas: List[float],
        filters: List[Type[AbstractFilter]], colormaps: List[Colormap],
        min_intensities: List[int], max_intensities: List[int], log: bool,
        use_precomputed: bool, **kwargs
    ):
        super().__init__(
            in_image, in_channels, in_z_slices, in_timepoints, out_format,
            out_width, out_height, 8, c_reduction, z_reduction, t_reduction,
            gammas, filters, colormaps, min_intensities, max_intensities, log,
            **kwargs
        )

        self.use_precomputed = use_precomputed

    def raw_view(self, c: int, z: int, t: int) -> ImagePixels:
        return self.in_image.thumbnail(
            self.out_width, self.out_height, c=c, z=z, t=t,
            precomputed=self.use_precomputed
        )


class ResizedResponse(ProcessedView):
    def __init__(
        self, in_image: Image, in_channels: List[int], in_z_slices: List[int],
        in_timepoints: List[int], out_format: OutputExtension, out_width: int,
        out_height: int, c_reduction: ChannelReduction,
        z_reduction: GenericReduction, t_reduction: GenericReduction,
        gammas: List[float], filters: List[Type[AbstractFilter]],
        colormaps: List[Colormap], min_intensities: List[int],
        max_intensities: List[int], log: bool, out_bitdepth: int,
        colorspace: Colorspace, **kwargs
    ):
        super().__init__(
            in_image, in_channels, in_z_slices, in_timepoints, out_format,
            out_width, out_height, out_bitdepth, c_reduction, z_reduction,
            t_reduction, gammas, filters, colormaps, min_intensities,
            max_intensities, log, colorspace, **kwargs
        )

    def raw_view(self, c: int, z: int, t: int) -> ImagePixels:
        return self.in_image.thumbnail(
            self.out_width, self.out_height, c=c, z=z, t=t, precomputed=False
        )


class WindowResponse(ProcessedView):
    def __init__(
        self, in_image: Image, in_channels: List[int], in_z_slices: List[int],
        in_timepoints: List[int], region: Region, out_format: OutputExtension,
        out_width: int, out_height: int, c_reduction: ChannelReduction,
        z_reduction: GenericReduction, t_reduction: GenericReduction,
        gammas: List[float], filters: List[Type[AbstractFilter]],
        colormaps: List[Colormap], min_intensities: List[int],
        max_intensities: List[int], log: bool, out_bitdepth: int,
        colorspace: Colorspace, annotations: Optional[ParsedAnnotations] = None,
        affine_matrix: Optional[np.ndarray] = None,
        annot_params: Optional[dict] = None, **kwargs
    ):
        super().__init__(
            in_image, in_channels, in_z_slices, in_timepoints, out_format,
            out_width, out_height, out_bitdepth, c_reduction, z_reduction,
            t_reduction, gammas, filters, colormaps, min_intensities,
            max_intensities, log, colorspace, **kwargs
        )

        self.region = region

        annot_params = annot_params if annot_params else dict()
        self.annotation_mode = annot_params.get('mode')
        self.annotations = annotations
        self.affine_matrix = affine_matrix
        self.background_transparency = annot_params.get('background_transparency')
        self.point_style = annot_params.get('point_cross')

    @property
    def colorspace_processing(self) -> bool:
        if (self.colorspace == Colorspace.AUTO
                and self.annotation_mode == AnnotationStyleMode.DRAWING
                and len(self.channels) == 1
                and not self.annotations.is_stroke_grayscale):
            return True
        return super(WindowResponse, self).colorspace_processing

    @property
    def new_colorspace(self) -> Colorspace:
        if (self.colorspace == Colorspace.AUTO
                and self.annotation_mode == AnnotationStyleMode.DRAWING
                and len(self.channels) == 1
                and not self.annotations.is_stroke_grayscale):
            return Colorspace.COLOR
        return super(WindowResponse, self).new_colorspace

    def process(self) -> ImagePixels:
        img = super(WindowResponse, self).process()

        if self.annotations and self.affine_matrix is not None:
            if self.annotation_mode == AnnotationStyleMode.CROP:
                mask = MaskRasterOp(
                    self.affine_matrix, self.out_width, self.out_height
                )(self.annotations)
                img = TransparencyMaskImgOp(
                    self.background_transparency, mask, self.out_bitdepth
                )(img)
            elif self.annotation_mode == AnnotationStyleMode.DRAWING:
                draw = DrawRasterOp(
                    self.affine_matrix, self.out_width,
                    self.out_height, self.point_style
                )(self.annotations)
                draw_background = DrawRasterOp.background_color(self.annotations)
                if self.colorspace_processing:
                    draw = ColorspaceImgOp(self.new_colorspace)(draw)
                img = DrawOnImgOp(draw, self.out_bitdepth, draw_background)(img)

        return img

    def raw_view(self, c: int, z: int, t: int) -> ImagePixels:
        return self.in_image.window(
            self.region, self.out_width, self.out_height, c=c, z=z, t=t
        )


class TileResponse(ProcessedView):
    def __init__(
        self, in_image: Image, in_channels: List[int], in_z_slices: List[int],
        in_timepoints: List[int], tile_region: Tile, out_format: OutputExtension,
        out_width: int, out_height: int, c_reduction: ChannelReduction,
        z_reduction: GenericReduction, t_reduction: GenericReduction,
        gammas: List[float], filters: List[Type[AbstractFilter]],
        colormaps: List[Colormap], min_intensities: List[int],
        max_intensities: List[int], log: bool, **kwargs
    ):
        super().__init__(
            in_image, in_channels, in_z_slices, in_timepoints, out_format,
            out_width, out_height, 8, c_reduction, z_reduction, t_reduction,
            gammas, filters, colormaps, min_intensities, max_intensities,
            log, **kwargs
        )

        # Tile (region)
        self.tile_region = tile_region

    def raw_view(self, c: int, z: int, t: int) -> ImagePixels:
        return self.in_image.tile(self.tile_region, c=c, z=z, t=t)


class AssociatedResponse(ImageResponse):
    def __init__(
        self, in_image: Image, associated_key: AssociatedName, out_width: int,
        out_height: int, out_format: OutputExtension, **kwargs
    ):
        super().__init__(in_image, out_format, out_width, out_height, **kwargs)
        self.associated_key = associated_key

    def associated_image(self) -> ImagePixels:
        if self.associated_key == AssociatedName.macro:
            associated = self.in_image.macro(self.out_width, self.out_height)
        elif self.associated_key == AssociatedName.label:
            associated = self.in_image.label(self.out_width, self.out_height)
        else:
            associated = self.in_image.thumbnail(
                self.out_width, self.out_height, precomputed=True
            )

        return associated

    def process(self) -> ImagePixels:
        img = self.associated_image()
        img = ResizeImgOp(self.out_width, self.out_height)(img)
        return img


class MaskResponse(ImageResponse):
    def __init__(
        self, in_image: Image, annotations: ParsedAnnotations,
        affine_matrix: np.ndarray, out_width: int, out_height: int,
        out_bitdepth: int, out_format: OutputExtension, **kwargs
    ):
        super().__init__(
            in_image, out_format, out_width, out_height,
            out_bitdepth, **kwargs
        )

        self.annotations = annotations
        self.affine_matrix = affine_matrix

    def process(self) -> ImagePixels:
        return MaskRasterOp(
            self.affine_matrix, self.out_width, self.out_height
        )(self.annotations)


class DrawingResponse(MaskResponse):
    def __init__(
        self, in_image: Image, annotations: ParsedAnnotations,
        affine_matrix: np.ndarray, point_style: PointCross,
        out_width: int, out_height: int, out_bitdepth: int,
        out_format: OutputExtension, **kwargs
    ):
        super().__init__(
            in_image, annotations, affine_matrix, out_width,
            out_height, out_bitdepth, out_format, **kwargs
        )

        self.point_style = point_style

    def process(self) -> ImagePixels:
        return DrawRasterOp(
            self.affine_matrix, self.out_width,
            self.out_height, self.point_style
        )(self.annotations)


class ColormapRepresentationResponse(ImageResponse):
    def __init__(
        self, colormap: Colormap, out_width: int, out_height: int,
        out_format: OutputExtension, **kwargs
    ):
        super().__init__(None, out_format, out_width, out_height, **kwargs)
        self.colormap = colormap

    def process(self) -> ImagePixels:
        return self.colormap.as_image(self.out_width, self.out_height)
