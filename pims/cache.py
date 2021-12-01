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
import asyncio
import copy
import hashlib
import inspect
from enum import Enum
from functools import partial, wraps
from typing import Any, Callable, Dict, KeysView, List, Optional, Type, Union

import aioredis
from fastapi_cache import Coder, FastAPICache, default_key_builder
from fastapi_cache.backends import Backend
from fastapi_cache.backends.redis import RedisBackend as RedisBackend_
from fastapi_cache.coder import JsonCoder, PickleCoder
from starlette.concurrency import run_in_threadpool
from starlette.responses import Response

from pims.api.utils.mimetype import VISUALISATION_MIMETYPES, get_output_format
from pims.config import get_settings

HEADER_CACHE_CONTROL = "Cache-Control"
HEADER_ETAG = "ETag"
HEADER_IF_NONE_MATCH = "If-None-Match"

CACHE_KEY_PIMS_VERSION = "PIMS_VERSION"


class RedisBackend(RedisBackend_):
    async def exists(self, key) -> bool:
        return await self.redis.exists(key)


class PIMSCache(FastAPICache):
    _enabled = False

    @classmethod
    async def init(
        cls, backend, prefix: str = "", expire: int = None, coder: Coder = JsonCoder,
        key_builder: Callable = default_key_builder
    ):
        super().init(backend, prefix, expire, coder, key_builder)
        try:
            await cls._backend.get(CACHE_KEY_PIMS_VERSION)
            cls._enabled = True
        except ConnectionError:
            cls._enabled = False

    @classmethod
    def get_backend(cls):
        if not cls._enabled:
            raise ConnectionError("Cache is not enabled.")
        return cls._backend

    @classmethod
    def is_enabled(cls):
        return cls._enabled


def get_cache() -> Backend:
    return PIMSCache.get_backend()


async def _startup_cache(pims_version):
    settings = get_settings()
    if not settings.cache_enabled:
        return

    redis = aioredis.from_url(settings.cache_url)
    await PIMSCache.init(
        RedisBackend(redis), prefix="pims-cache",
        key_builder=all_kwargs_key_builder
    )

    # Flush the cache if persistent and PIMS version has changed.
    cache = get_cache()
    cached_version = await cache.get(CACHE_KEY_PIMS_VERSION)
    if cached_version != pims_version:
        await cache.clear(PIMSCache.get_prefix())
        await cache.set(CACHE_KEY_PIMS_VERSION, pims_version)


async def exec_func_async(func, *args, **kwargs):
    is_async = asyncio.iscoroutinefunction(func)
    if is_async:
        return await func(*args, **kwargs)
    else:
        return await run_in_threadpool(func, *args, **kwargs)


def all_kwargs_key_builder(func, kwargs, excluded_parameters, prefix):
    copy_kwargs = kwargs.copy()
    if excluded_parameters is None:
        excluded_parameters =  []
    for excluded in excluded_parameters:
        if excluded in copy_kwargs:
            copy_kwargs.pop(excluded)

    hashable = f"{func.__module__}:{func.__name__}"
    for k, v in copy_kwargs.items():
        if isinstance(v, Enum):
            v = v.value
        hashable += f":{k}={str(v)}"

    hashed = hashlib.md5(hashable.encode()).hexdigest()
    cache_key = f"{prefix}:{hashed}"
    return cache_key


def _image_response_key_builder(
    func, kwargs, excluded_parameters, prefix, supported_mimetypes
):
    copy_kwargs = kwargs.copy()
    headers = copy_kwargs.get('headers')
    if headers and 'headers' not in excluded_parameters:
        # Find true output extension
        accept = headers.get('accept')
        extension = copy_kwargs.get('extension')
        format = get_output_format(extension, accept, supported_mimetypes)
        copy_kwargs['extension'] = format

        # Extract other custom headers
        extra_headers = ('safe_mode', 'annotation_origin')
        for eh in extra_headers:
            v = headers.get(eh)
            if v:
                copy_kwargs[f"headers.{eh}"] = v
        del copy_kwargs['headers']

    return all_kwargs_key_builder(
        func, copy_kwargs, excluded_parameters, prefix
    )


def default_cache_control_builder(ttl=0):
    """
    Cache-Control header is not intuitive.
    * https://developer.mozilla.org/en-US/docs/Web/HTTP/Headers/Cache-Control
    * https://web.dev/http-cache/#flowchart
    * https://jakearchibald.com/2016/caching-best-practices/
    * https://www.azion.com/en/blog/what-is-http-caching-and-how-does-it-work/
    """
    params = ["private", "must-revalidate"]
    if ttl:
        params += [f"max-age={ttl}"]
    return ','.join(params)


def cache(
    expire: int = None,
    vary: Optional[List] = None,
    codec: Type[Coder] = None,
    key_builder: Callable = None,
    cache_control_builder: Callable = None
):
    def wrapper(func: Callable):
        @wraps(func)
        async def inner(*args, **kwargs):
            nonlocal expire
            nonlocal vary
            nonlocal codec
            nonlocal key_builder
            nonlocal cache_control_builder
            signature = inspect.signature(func)
            bound_args = signature.bind_partial(*args, **kwargs)
            bound_args.apply_defaults()
            all_kwargs = bound_args.arguments
            request = all_kwargs.pop("request", None)
            response = all_kwargs.pop("response", None)

            if not PIMSCache.is_enabled() or \
                    (request and request.headers.get(HEADER_CACHE_CONTROL) == "no-store"):
                return await exec_func_async(func, *args, **kwargs)

            expire = expire or PIMSCache.get_expire()
            codec = codec or PIMSCache.get_coder()
            key_builder = key_builder or PIMSCache.get_key_builder()
            backend = PIMSCache.get_backend()
            prefix = PIMSCache.get_prefix()

            cache_key = key_builder(func, all_kwargs, vary, prefix)
            ttl, encoded = await backend.get_with_ttl(cache_key)
            if not request:
                if encoded is not None:
                    return codec.decode(encoded)
                data = await exec_func_async(func, *args, **kwargs)
                encoded = codec.encode(data)
                await backend.set(
                    cache_key, encoded,
                    expire or PIMSCache.get_expire()
                )
                return data

            if_none_match = request.headers.get(HEADER_IF_NONE_MATCH.lower())
            if encoded is not None:
                if response:
                    cache_control_builder = \
                        cache_control_builder or default_cache_control_builder
                    response.headers[HEADER_CACHE_CONTROL] = \
                        cache_control_builder(ttl=ttl)
                    etag = f"W/{hash(encoded)}"
                    response.headers[HEADER_ETAG] = etag
                    if if_none_match == etag:
                        response.status_code = 304
                        return response
                decoded = codec.decode(encoded)
                if isinstance(decoded, Response):
                    decoded.headers[HEADER_CACHE_CONTROL] = \
                        response.headers.get(HEADER_CACHE_CONTROL)
                    decoded.headers[HEADER_ETAG] = \
                        response.headers.get(HEADER_ETAG)
                return decoded

            data = await exec_func_async(func, *args, **kwargs)
            encoded = codec.encode(data)
            await backend.set(cache_key, encoded, expire)

            if response:
                cache_control_builder = \
                    cache_control_builder or default_cache_control_builder
                response.headers[HEADER_CACHE_CONTROL] = \
                    cache_control_builder(ttl=expire)
                etag = f"W/{hash(encoded)}"
                response.headers[HEADER_ETAG] = etag
                if isinstance(data, Response):
                    data.headers[HEADER_CACHE_CONTROL] = \
                        response.headers.get(HEADER_CACHE_CONTROL)
                    data.headers[HEADER_ETAG] = \
                        response.headers.get(HEADER_ETAG)

            return data

        return inner

    return wrapper


def cache_image_response(
    expire: int = None,
    vary: Optional[List] = None,
    supported_mimetypes=None
):
    if supported_mimetypes is None:
        supported_mimetypes = VISUALISATION_MIMETYPES
    key_builder = partial(
        _image_response_key_builder, supported_mimetypes=supported_mimetypes
    )
    codec = PickleCoder
    return cache(expire, vary, codec, key_builder)


DictCache = Dict[str, Any]


class SimpleDataCache:
    """
    A simple wrapper to add caching mechanisms to a class.
    """
    def __init__(self, existing_cache: DictCache = None):
        self._cache = dict()

        if existing_cache is dict:
            self._cache = copy.deepcopy(existing_cache)

    def cache_value(self, key: str, value: Any, force: bool = False):
        """
        Cache a value at some key in the cache.

        Parameters
        ----------
        key
            The cache key
        value
            The content to cache
        force
            Whether to force to re-cache content if key is already cached.
        """
        if force or key not in self._cache:
            self._cache[key] = value

    def cache_func(self, key: str, delayed_func: Callable, *args, **kwargs):
        """
        Cache a function result at some key in the cache.

        Parameters
        ----------
        key
            The cache key
        delayed_func
            The function to call to get result to cache
        args
            The arguments to pass to `delayed_func`
        kwargs
            The keyword arguments to pass to `delayed_func`
        """
        self.cache_value(key, delayed_func(*args, **kwargs))

    def get_cached(
        self, key: str, delayed_func_or_value: Union[Callable, Any],
        *args, **kwargs
    ) -> Any:
        """
        Get cache content at given key, otherwise cache new content for this key.

        Parameters
        ----------
        key
            The cache key
        delayed_func_or_value
            If key is not in cache, cache the function result (if it is callable)
            or the variable content.
        args
            The arguments to pass to the delayed function if it is callable
        kwargs
            The keyword arguments to pass to the delayed function if it is
            callable.

        Returns
        -------
        content
            Cached content
        """
        if not self.is_in_cache(key):
            if callable(delayed_func_or_value):
                delayed_func = delayed_func_or_value
                self.cache_func(key, delayed_func, *args, **kwargs)
            else:
                value = delayed_func_or_value
                self.cache_value(key, value)
        return self._cache[key]

    @property
    def cache(self) -> DictCache:
        return self._cache

    @property
    def cached_keys(self) -> KeysView[str]:
        return self._cache.keys()

    def is_in_cache(self, key) -> bool:
        return key in self._cache

    def clear_cache(self):
        self._cache.clear()
