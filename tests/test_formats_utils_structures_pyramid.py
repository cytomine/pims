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
from pims.formats.utils.structures.pyramid import Pyramid, PyramidTier
from pims.processing.region import Region


def test_pyramid_tier():
    tier = PyramidTier(1000, 2000, 256, Pyramid())
    assert tier.n_pixels == 1000 * 2000
    assert tier.factor == (1.0, 1.0)


def test_pyramid():
    p = Pyramid()
    p.insert_tier(1000, 2000, 256)
    assert p.n_levels == 1
    assert p.max_level == 0
    assert p.n_zooms == 1
    assert p.max_zoom == 0

    p.insert_tier(100, 200, 256)
    assert p.n_levels == 2
    assert p.get_tier_at_level(1).n_pixels == 100 * 200
    assert p.get_tier_at_level(1).level == 1
    assert p.get_tier_at_level(1).zoom == 0

    p.insert_tier(500, 1000, 256)
    assert p.n_levels == 3
    assert p.get_tier_at_level(1).n_pixels == 500 * 1000
    assert p.get_tier_at_level(1).level == 1
    assert p.get_tier_at_level(1).zoom == 1
    assert p.get_tier_at_level(2).n_pixels == 100 * 200
    assert p.get_tier_at_level(2).level == 2
    assert p.get_tier_at_level(2).zoom == 0


def test_pyramid_tier_indexes():
    tier = PyramidTier(1000, 2000, 256, Pyramid())
    assert tier.max_tx == 4
    assert tier.max_ty == 8
    assert tier.max_ti == 32

    assert tier.txty2ti(0, 0) == 0
    assert tier.txty2ti(3, 7) == 31

    assert tier.ti2txty(0) == (0, 0)
    assert tier.ti2txty(31) == (3, 7)

    assert tier.get_txty_tile(0, 0) == Region(0, 0, 256, 256)
    assert tier.get_txty_tile(3, 7) == Region(1792, 768, 1000 - 768, 2000 - 1792)

    assert tier.get_ti_tile(0) == Region(0, 0, 256, 256)
    assert tier.get_ti_tile(31) == Region(1792, 768, 1000 - 768, 2000 - 1792)
