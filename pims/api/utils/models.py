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

from enum import Enum
from typing import List, Optional, Union

from fastapi import Query
from pydantic import BaseModel, Field, confloat, conint

from pims.utils.color import Color


class BaseDependency:
    def dict(self):
        return vars(self)


class CollectionSize(BaseModel):
    size: int = Field(..., description='The collection size')


class FormatId(BaseModel):
    __root__: str = Field(..., description='Unique format identifier', example='VMS')


class ZoomOrLevel(BaseModel):
    __root__: conint(ge=0) = Field(..., example=0)


class SingleChannelIndex(BaseModel):
    """
    A single channel index. **By default**, all channels are considered.
    """
    __root__: conint(ge=0)


class SingleZSliceIndex(BaseModel):
    """
    A single focal plane index. **By default**, the median focal plane is considered.
    """
    __root__: conint(ge=0)


class SingleTimepointIndex(BaseModel):
    """
    A single timepoint index. **By default**, the first timepoint considered.
    """
    __root__: conint(ge=0)


class PlaneSelectionQueryParams(BaseDependency):
    def __init__(
        self,
        channels: Optional[conint(ge=0)] = Query(None),
        z_slices: Optional[conint(ge=0)] = Query(None),
        timepoints: Optional[conint(ge=0)] = Query(None)
    ):
        self.channels = channels
        self.z_slices = z_slices
        self.timepoints = timepoints


class IntensitySelectionEnum(str, Enum):
    AUTO_IMAGE = 'AUTO_IMAGE'
    AUTO_PLANE = 'AUTO_PLANE'
    STRETCH_IMAGE = 'STRETCH_IMAGE'
    STRETCH_PLANE = 'STRETCH_PLANE'


class IntensitySelection(BaseModel):
    __root__: Union[
        IntensitySelectionEnum,
        conint(ge=0),
        List[Union[IntensitySelectionEnum, conint(ge=0)]],
    ]


class Gamma(BaseModel):
    __root__: confloat(ge=0.0, le=10.0)


class GammaList(BaseModel):
    """
    Gamma performs a non-linear histogram adjustment.
    Pixel intensities in the original image are raised
    to the power of the gamma value.

    If `gamma < 1`, faint objects become more intense
    while bright objects do not.

    If `gamma > 1`, medium-intensity objects become fainter
    while bright objects do not.
    """
    __root__: Union[Gamma, List[Gamma]]


class FilterId(BaseModel):
    """
    A unique case-insensitive identifier for an image filter
    """
    __root__: str = Field(
        ..., example='OTSU'
    )


class FilterIdList(BaseModel):
    """
    An image filter is used to change the appearance of an image
    and helps at understanding the source image.

    Valid filter names can be found with the endpoint `/filters`.
    """
    __root__: Union[FilterId, List[FilterId]]


class FilterType(str, Enum):
    """
    * `LOCAL` - The image filter uses the neighborhood of every pixels.
    * `GLOBAL` - The image filter uses histogram-derived techniques.
    * `PIXEL` - The image filter works pixel per pixel.

    """
    LOCAL = 'LOCAL'
    GLOBAL = 'GLOBAL'
    PIXEL = 'PIXEL'


class ColormapEnum(str, Enum):
    """
    * `NONE` - No colormap is applied, all channels are grayscale.
    * `DEFAULT` - The colormap(s) determined by image metadata and/or format.
    """
    NONE = 'NONE'
    DEFAULT = 'DEFAULT'
    DEFAULT_INVERTED = '!DEFAULT'


class ColormapId(BaseModel):
    """
    A unique case-insensitive identifier for a colormap.
    Pre-defined colormap names can be found with the endpoint `/colormaps`.
    CSS named and hexadecimal colors are valid colormap names (monotonic linear colormap).
    Colormaps can be inverted by prepending the colormap name with `!`.
    """
    __root__: Union[str, ColormapEnum] = Field(
        ..., examples=['JET', '!#f00', 'red']
    )


class ColormapIdList(BaseModel):
    """
    A colormap is a function that maps the colors of the original image (source)
    to the colors of the response image. The usage of colormap produces
    a false-color representation of the original image and helps at
    understanding the image.

    Valid colormap names can be found with the endpoint `/colormaps`.
    """
    __root__: Union[ColormapId, List[ColormapId]]


class ExistingColormapId(BaseModel):
    """
    A unique case-insensitive identifier for a colormap.
    Pre-defined colormap names can be found with the endpoint `/colormaps`.
    CSS named and hexadecimal colors are valid colormap names (monotonic linear colormap).
    Colormaps can be inverted by prepending the colormap name with `!`.
    """
    __root__: str = Field(
        ..., examples=['JET', '!#f00', 'red']
    )


class ImageIn(BaseModel):
    channels: Optional[SingleChannelIndex] = None
    z_slices: Optional[SingleZSliceIndex] = None
    timepoints: Optional[SingleTimepointIndex] = None
    min_intensities: Optional[IntensitySelection] = None
    max_intensities: Optional[IntensitySelection] = None
    colormaps: Optional[ColormapIdList] = ColormapEnum.DEFAULT
    filters: Optional[FilterIdList] = None
    gammas: GammaList = 1.0

    class Config:
        __mi_doc = "Intensity in the original image used as minimum intensity (black) to create the response." \
                   "As a consequence, original image intensities lower than this value will be black in the response." \
                   "\n\n" \
                   "Maximum allowed value depends on image pixel type and is equal to `2 * pow(pixel type)`.\n" \
                   "Minimum intensity is closely related to the concepts of brightness and contrast." \
                   "\n\n" \
                   "Brightness is the visual perception of reflected light while contrast is the separation of the " \
                   "lightest and darkest parts of an image. A minimum intensity increase leads to:\n" \
                   "* a brightness decrease, which refers to an image's decreased luminance.\n" \
                   "* a contrast increase, which darken shadows and lighten highlights.\n\n" \
                   "Enumeration supported values:\n" \
                   "* `AUTO_IMAGE` - If image pixel type uses 8 bits, `min_intensity=0`. " \
                   "Otherwise, the behavior is `STRETCH_IMAGE`.\n" \
                   "* `AUTO_PLANE` - If image pixel type uses 8 bits, `min_intensity=0`. " \
                   "Otherwise, the behavior is `STRETCH_PLANE`.\n" \
                   "* `STRETCH_IMAGE` - Set `min_intensity` to lowest intensity in the original image, " \
                   "for each channel.\n" \
                   "* `STRETCH_PLANE` - Set `min_intensity` to lowest intensity in the set of planes, for each channel."

        __ma_doc = "Intensity in the original image used as maximum intensity (white) to create the response." \
                   "As a consequence, original image intensities greater than this value will be white in the response." \
                   "\n\n" \
                   "Maximum allowed value depends on image pixel type and is equal to `2 * pow(pixel type)`.\n" \
                   "Maximum intensity is closely related to the concepts of brightness and contrast." \
                   "\n\n" \
                   "Brightness is the visual perception of reflected light while contrast is the separation of the " \
                   "lightest and darkest parts of an image. A maximum intensity increase leads to:\n" \
                   "* a brightness decrease, which refers to an image's decreased luminance.\n" \
                   "* a contrast decrease, which darken highlights and lighten shadows.\n\n" \
                   "Enumeration supported values:\n" \
                   "* `AUTO_IMAGE` - If image pixel type uses 8 bits, `max_intensity=255`. " \
                   "Otherwise, the behavior is `STRETCH_IMAGE`.\n" \
                   "* `AUTO_PLANE` - If image pixel type uses 8 bits, `max_intensity=255`. " \
                   "Otherwise, the behavior is `STRETCH_PLANE`.\n" \
                   "* `STRETCH_IMAGE` - Set `min_intensity` to greatest intensity in the original image, " \
                   "for each channel.\n" \
                   "* `STRETCH_PLANE` - Set `min_intensity` to greatest intensity in the set of planes, " \
                   "for each channel."
        fields = {
            "min_intensities": {
                "description": __mi_doc
            },
            "max_intensities": {
                "description": __ma_doc
            }
        }


class ImageOut(BaseModel):
    height: Optional[Union[conint(gt=1), confloat(gt=0.0, le=1.0)]] = None
    width: Optional[Union[conint(gt=1), confloat(gt=0.0, le=1.0)]] = None
    length: Optional[Union[conint(gt=1), confloat(gt=0.0, le=1.0)]] = None

    class Config:
        fields = {
            "height": {
                "description": "Height of the thumbnail. Width is adjusted to preserve the "
                               "aspect ratio. "
                               "**Takes precedence over `width` and `length`.**"
            },
            "width": {
                "description": "Width of the thumbnail. Height is adjusted to preserve the "
                               "aspect ratio. "
                               "**Takes precedence over `length`.**"
            },
            "length": {
                "description": "Length of the largest side of the thumbnail. "
                               "The other dimension is adjusted to preserve the aspect ratio. "
                               "**Ignored if other size-related parameter such as `width` or "
                               "`height` is present.** "
            }
        }


class ImageInDisplay(ImageIn):
    min_intensities: Optional[IntensitySelection] = IntensitySelectionEnum.AUTO_IMAGE
    max_intensities: Optional[IntensitySelection] = IntensitySelectionEnum.AUTO_IMAGE
    log: bool = Field(
        False,
        description='Apply a logarithmic scale on image data to ease observation '
                    'of high dynamic range images such as 16-bit images.',
    )


class ImageOpsDisplayQueryParams(BaseDependency):
    def __init__(
        self,
        gammas: Optional[List[confloat(ge=0.0, le=10.0)]] = Query([1.0]),
        min_intensities: Optional[List[Union[IntensitySelectionEnum, conint(ge=0)]]] = Query(
            [
                IntensitySelectionEnum.AUTO_IMAGE]
        ),
        max_intensities: Optional[List[Union[IntensitySelectionEnum, conint(ge=0)]]] = Query(
            [
                IntensitySelectionEnum.AUTO_IMAGE]
        ),
        colormaps: Optional[List[Union[str, ColormapEnum]]] = Query([ColormapEnum.DEFAULT]),
        filters: Optional[List[str]] = Query(None),
        log: bool = Query(False),
    ):
        self.gammas = gammas
        self.min_intensities = min_intensities
        self.max_intensities = max_intensities
        self.colormaps = colormaps
        self.filters = filters
        self.log = log


class ImageOutDisplay(ImageOut):
    length: Optional[Union[conint(gt=1), confloat(gt=0.0, le=1.0)]] = 256


class ImageOutDisplayQueryParams(BaseDependency):
    def __init__(
        self,
        height: Optional[Union[conint(gt=1), confloat(gt=0.0, le=1.0)]] = Query(None),
        width: Optional[Union[conint(gt=1), confloat(gt=0.0, le=1.0)]] = Query(None),
        length: Optional[Union[conint(gt=1), confloat(gt=0.0, le=1.0)]] = Query(256)
    ):
        self.length = length
        self.width = width
        self.height = height


class BitDepthEnum(str, Enum):
    AUTO = 'AUTO'


class BitDepth(BaseModel):
    """
    The target bit depth. It is a best-effort parameter as supported bit depths
    depend on target content type.
    * `AUTO` - Try to set target bit depth equal to the source bit depth.
    """
    __root__: Union[BitDepthEnum, int] = Field(BitDepthEnum.AUTO)


class Colorspace(str, Enum):
    """
    The target colorspace. It is a best-effort parameter as supported
    colorspace depend on target content type.

    * `GRAY` - Return the target in grayscale, using luminance if it was
    initially a color image.
    * `COLOR` - Return the target in RGB colors.
    * `AUTO` - Automatically returns the target in the best colorspace.

    If the source is grayscale and image manipulation do not use colors,
    it returns the target in grayscale. Otherwise, target is in RGB colors.
    """
    GRAY = 'GRAY'
    COLOR = 'COLOR'
    AUTO = 'AUTO'


class ImageInProcessing(ImageIn):
    bits: BitDepth = BitDepthEnum.AUTO
    colorspace: Colorspace = Colorspace.AUTO


class ImageOpsProcessingQueryParams(BaseDependency):
    def __init__(
        self,
        gammas: Optional[List[confloat(ge=0.0, le=10.0)]] = Query([1.0]),
        min_intensities: Optional[List[Union[IntensitySelectionEnum, conint(ge=0)]]] = Query(None),
        max_intensities: Optional[List[Union[IntensitySelectionEnum, conint(ge=0)]]] = Query(None),
        colormaps: Optional[List[Union[str, ColormapEnum]]] = Query([ColormapEnum.DEFAULT]),
        filters: Optional[List[str]] = Query(None),
        bits: Optional[Union[BitDepthEnum, int]] = Query(BitDepthEnum.AUTO),
        colorspace: Optional[Colorspace] = Query(Colorspace.AUTO)
    ):
        self.gammas = gammas
        self.min_intensities = min_intensities
        self.max_intensities = max_intensities
        self.colormaps = colormaps
        self.filters = filters
        self.bits = bits
        self.colorspace = colorspace


class ImageOutProcessing(ImageOut):
    zoom: Optional[conint(ge=0)] = None
    level: Optional[conint(ge=0)] = None

    class Config:
        fields = {
            "zoom": {
                "description": "The zoom level to consider as thumbnail. Zoom 0 has the worst "
                               "resolution (smallest image, top of the image pyramid). Maximum "
                               "admissible zoom level depends on image. "
                               "**Takes precedence over `height`, `width` and `length`.**"
            },
            "level": {
                "description": "The tier level. Level 0 has the best resolution (largest image, "
                               "basis of the image pyramid). Maximum admissible tier level "
                               "depends on image. "
                               "**Takes precedence over `zoom`, `height`, `width` and `length`.**"
            }
        }


class ImageOutProcessingQueryParams(BaseDependency):
    def __init__(
        self,
        zoom: Optional[conint(ge=0)] = Query(None),
        level: Optional[conint(ge=0)] = Query(None)
    ):
        self.zoom = zoom
        self.level = level


class ResizedRequest(ImageInProcessing, ImageOutProcessing):
    pass


class ThumbnailRequest(ImageInDisplay, ImageOutDisplay):
    use_precomputed: bool = Field(
        True,
        description="Whether the precomputed thumbnail (associated file) "
                    "has to be used if it is available, or not.",
    )


class TierIndexType(str, Enum):
    """
    How to interpret `reference_tier_index`, either as a zoom index,
    either as a level index.
    """
    LEVEL = 'LEVEL'
    ZOOM = 'ZOOM'


class ReferenceTierIndex(BaseModel):
    reference_tier_index: Optional[int] = Field(
        None,
        description="The index (zoom or level) denoting the tier "
                    "to use as reference coordinate system "
                    "for the input region. By default, best resolution tier "
                    "(maximum zoom/lowest level) is used.",
    )
    tier_index_type: TierIndexType = Field(
        TierIndexType.LEVEL,
        description='How to interpret `reference_tier_index`, '
                    'either as a zoom index, either as a level index.',
    )


class RegionCoordinate(BaseModel):
    """
    A coordinate in pixels in the reference tier.
    """
    __root__: conint(ge=0) = Field(..., example=256)


class RegionLength(BaseModel):
    """
    A length in pixels in the reference tier.
    """
    __root__: conint(ge=1) = Field(..., example=256)


class WindowRegion(ReferenceTierIndex):
    """
    The input region in the reference tier.
    """

    top: RegionCoordinate
    left: RegionCoordinate
    width: RegionLength
    height: RegionLength


class TileIndex(BaseModel):
    """
    The tile index is the position of the tile in the given image pyramid tier.
    It is computed as `n * n_x_tiles + m` where
    * `n_x_tiles` is the number of tiles along the horizontal axis at given tier.
    * `m` is the tile position along the horizontal axis at given tier (0 is left).
    * `n` is the tile position along the vertical axis at given tier (0 is top).
    """
    __root__: conint(ge=0) = Field(...)


class TileX(BaseModel):
    """
    The tile position along the horizontal axis at given tier (0 is left).
    """
    __root__: conint(ge=0) = Field(...)


class TileY(BaseModel):
    """
    The tile position along the vertical axis at given tier (0 is top).
    """
    __root__: conint(ge=0) = Field(...)


class WindowTileCoord(ReferenceTierIndex):
    tx: TileX
    ty: TileY


class WindowTileIndex(ReferenceTierIndex):
    ti: TileIndex


class Annotation(BaseModel):
    geometry: str = Field(
        ...,
        description='A geometry described in Well-known text (WKT)',
        example='POINT(10 10)',
    )
    fill_color: Optional[Color] = Field(
        Color("white"),
        description='A color to fill the annotation',
        example='#FF00FF'
    )
    stroke_color: Optional[Color] = Field(
        None,
        description='A color for the annotation stroke'
    )
    stroke_width: Optional[conint(ge=0, le=10)] = Field(
        0,
        description='A width for the annotation stroke'
    )


class Annotations(BaseModel):
    __root__: Union[List[Annotation], Annotation]


class PointCross(str, Enum):
    """
    Point geometries have no area. Possible representation as a drawing are:
    * `CROSS` - A regular cross, hiding point location
    * `CROSSHAIR` - A cross but whose intersection is removed to show the point location
    * `CIRCLE` - A circle around the point location
    This parameter has no effect on non-point geometries.
    """
    CROSS = 'CROSS'
    CROSSHAIR = 'CROSSHAIR'
    CIRCLE = 'CIRCLE'


class PointEnvelopeLength(BaseModel):
    """
    Point geometries have no area. An envelope must be specified to 
    extract some image data.
    This envelope is a square of length given by this parameter, 
    whose the center is the given point.
    
    This parameter has no effect on non-point geometries.
    """
    __root__: int = 100


class AnnotationBgTransparency(BaseModel):
    """
    The background transparency. 100 means transparent background. 
    When transparency is used, the target content type must be 
    an image format supporting transparency.
    """
    __root__: conint(ge=0, le=100) = 0


class AnnotationStyleMode(str, Enum):
    CROP = 'CROP'
    MASK = 'MASK'
    DRAWING = 'DRAWING'


class AnnotationStyle(BaseModel):
    mode: AnnotationStyleMode
    point_envelope_length: Optional[PointEnvelopeLength]
    point_cross: Optional[PointCross] = PointCross.CROSS
    background_transparency: Optional[AnnotationBgTransparency]


class WindowRequest(ImageInProcessing, ImageOutProcessing):
    region: Union[WindowRegion, WindowTileIndex, WindowTileCoord]
    annotations: Optional[Annotations] = None
    annotation_style: Optional[AnnotationStyle] = None


class AnnotationContextFactor(BaseModel):
    """
    The context factor is the number by which the spatial region 
    is given by the rectangular envelope of all geometries is multiplied.
    """
    __root__: float = 1


class AnnotationTrySquare(BaseModel):
    """
    Try to adapt other parameters (such as input spatial region,
    context factor, target size) to return a square image.
    """
    __root__: bool = False


class AnnotationCropRequest(ImageInProcessing, ImageOutProcessing):
    annotations: Annotations
    context_factor: Optional[AnnotationContextFactor] = 1.0
    background_transparency: Optional[AnnotationBgTransparency] = 0


class AnnotationMaskRequest(ImageOutProcessing):
    annotations: Annotations
    context_factor: Optional[AnnotationContextFactor] = 1.0


class AnnotationDrawingRequest(ImageInDisplay, ImageOutProcessing):
    annotations: Annotations
    context_factor: Optional[AnnotationContextFactor] = 1.0
    try_square: Optional[AnnotationTrySquare] = False
    point_envelope_length: Optional[PointEnvelopeLength] = 100
    point_cross: Optional[PointCross] = PointCross.CROSS


class TargetZoom(BaseModel):
    """
    The zoom level to consider for the target.
    Zoom 0 has the worst resolution (smallest image, top of the image pyramid).
    Maximum admissible zoom level depends on image.
    """
    __root__: conint(ge=0)


class TargetLevel(BaseModel):
    """
    The tier level to consider for the target.
    Level 0 has the best resolution (largest image, basis of the image pyramid).
    Maximum admissible tier level depends on image.
    """
    __root__: conint(ge=0)


class TargetZoomTileIndex(BaseModel):
    zoom: TargetZoom
    ti: TileIndex


class TargetLevelTileIndex(BaseModel):
    level: TargetLevel
    ti: TileIndex


class TargetZoomTileCoordinates(BaseModel):
    zoom: TargetZoom
    tx: TileX
    ty: TileY


class TargetLevelTileCoordinates(BaseModel):
    level: TargetLevel
    tx: TileX
    ty: TileY


class TileRequest(ImageInDisplay):
    tile: Union[TargetZoomTileIndex, TargetZoomTileCoordinates,
                TargetLevelTileIndex, TargetLevelTileCoordinates]


class ChannelReduction(str, Enum):
    """
    Reduction function used to merge selected channels.
    """

    ADD = 'ADD'
    MIN = 'MIN'
    AVG = 'AVG'
    MAX = 'MAX'


class GenericReduction(str, Enum):
    MIN = 'MIN'
    AVG = 'AVG'
    MAX = 'MAX'


class AssociatedName(str, Enum):
    """
    The type of associated image.

    `macro` - A macro image (generally, in slide scanners, a low resolution picture of the entire slide)
    `label` - A label image (generally a barcode)
    `thumb` - A pre-computed thumbnail
    """

    macro = 'macro'
    label = 'label'
    thumb = 'thumb'


class HistogramType(str, Enum):
    FAST = "FAST"
    COMPLETE = "COMPLETE"
