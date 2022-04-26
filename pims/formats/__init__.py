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
from importlib import import_module
from inspect import isabstract, isclass
from pkgutil import iter_modules
from types import ModuleType
from typing import Dict, List, Type, Union

from importlib_metadata import EntryPoint, entry_points  # noqa

from pims.formats.utils.abstract import AbstractFormat

FORMAT_PLUGIN_PREFIX = 'pims_format_'
NON_PLUGINS_MODULES = ["pims.formats.utils"]
PLUGIN_GROUP = "pims.formats"

logger = logging.getLogger("pims.app")
logger.info("[green bold]Formats initialization...")


def _discover_format_plugins() -> List[Union[str, EntryPoint]]:
    """
    Discover format plugins in the Python env.
    Plugins are:
    * modules in `pims.formats`.
    * modules starting with `FORMAT_PLUGIN_PREFIX`.
    * packages having an entrypoint in group `PLUGIN_GROUP`.

    It follows conventions defined in
    https://packaging.python.org/guides/creating-and-discovering-plugins/

    Returns
    -------
    plugins
        The list of plugin module names or entry points
    """

    plugins = [
        name for _, name, _ in iter_modules(__path__, prefix="pims.formats.")
        if name not in NON_PLUGINS_MODULES
    ]
    print(f"plugins with prefix 'pims.formats': {plugins}")
    plugins += [
        name for _, name, _ in iter_modules()
        if name.startswith(FORMAT_PLUGIN_PREFIX)
    ]
    print(f"plugins with prefix 'pims.formats' and 'pims_format_': {plugins}")
    plugins += entry_points(group=PLUGIN_GROUP)
    print(f"all plugins (with entrypoints): {plugins}")
    plugin_names = [p.module if type(p) is EntryPoint else p for p in plugins]
    print(f"plugins names: {plugin_names}")
    print(f"number of plugins: {len(plugins)}")
    print(plugins[0], plugins[1])
    print(type(plugins[0]), type(plugins[1]))
    logger.info(
        f"[green bold]Format plugins: found {len(plugins)} plugin(s)[/] "
        f"[yellow]({', '.join(plugin_names)})"
    )
    return plugins


def _find_formats_in_module(mod: ModuleType) -> List[Type[AbstractFormat]]:
    """
    Find all Format classes in a module.

    Parameters
    ----------
    mod: module
        The module to analyze

    Returns
    -------
    formats: list
        The format classes
    """
    formats = list()
    for _, name, _ in iter_modules(mod.__path__):
        submodule_name = f"{mod.__name__}.{name}"
        try:
            for var in vars(import_module(submodule_name)).values():
                if isclass(var) and \
                        issubclass(var, AbstractFormat) and \
                        not isabstract(var) and \
                        'Abstract' not in var.__name__:
                    format = var
                    formats.append(format)
                    format.init()

                    logger.info(
                        f"[green] * [yellow]{format.get_identifier()} "
                        f"- {format.get_name()}[/] imported."
                    )
        except ImportError as e:
            logger.error(
                f"{submodule_name} submodule cannot be checked for "
                f"formats !", exc_info=e
            )
    return formats


def _get_all_formats() -> List[Type[AbstractFormat]]:
    """
    Find all Format classes in modules specified in FORMAT_PLUGINS.

    Returns
    -------
    formats: list
        The format classes
    """
    formats = list()
    for plugin in FORMAT_PLUGINS:
        entrypoint_plugin = type(plugin) is EntryPoint

        module_name = plugin.module if entrypoint_plugin else plugin
        logger.info(
            f"[green bold]Importing formats from "
            f"[yellow]{module_name}[/] plugin..."
        )

        if entrypoint_plugin:
            module = plugin.load()
        else:
            module = import_module(module_name)
        formats.extend(_find_formats_in_module(module))

    return formats


FormatsByExt = Dict[str, Type[AbstractFormat]]


FORMAT_PLUGINS: List[Union[str, EntryPoint]] = _discover_format_plugins()
FORMATS: FormatsByExt = {
    f.get_identifier(): f
    for f in _get_all_formats()
}
