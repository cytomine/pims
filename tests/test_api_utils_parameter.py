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
import os

import pytest

from pims.api.utils.parameter import filepath2path, path2filepath
from pims.files.file import Path


@pytest.mark.parametrize("filepath", ("/abc", "abc", "abc/foo"))
def test_filepath2path(app, settings, filepath):
    assert str(filepath2path(filepath, settings)) == os.path.join(settings.root, filepath)


@pytest.mark.parametrize("rootpath", ("/abc", "abc", "abc/foo/"))
def test_path2filepath(app, settings, rootpath):
    fake_settings = settings.copy()
    fake_settings.root = rootpath
    path = Path(rootpath) / "dir/file"
    assert path2filepath(path, fake_settings) == "dir/file"
