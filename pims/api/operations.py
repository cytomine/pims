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
import os
import re
import traceback
from typing import Optional
import aiofiles

from cytomine import Cytomine
from cytomine.models import (
    Project, ProjectCollection, Storage, UploadedFile
)
from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, Query, UploadFile
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse
from starlette.formparsers import MultiPartMessage, MultiPartParser, _user_safe_decode

from pims.api.exceptions import (
    AuthenticationException, BadRequestException, CytomineProblem,
    check_representation_existence
)
from pims.api.utils.cytomine_auth import (
    get_this_image_server, parse_authorization_header,
    parse_request_token, sign_token
)
from pims.api.utils.parameter import filepath_parameter, imagepath_parameter, sanitize_filename
from pims.api.utils.response import serialize_cytomine_model
from pims.config import Settings, get_settings
from pims.files.archive import make_zip_archive
from pims.files.file import Path
from pims.importer.importer import run_import
from pims.importer.listeners import CytomineListener
from pims.tasks.queue import Task, send_task
from pims.utils.iterables import ensure_list
from pims.utils.strings import unique_name_generator

try:
    import multipart
    from multipart.multipart import parse_options_header
except ModuleNotFoundError:  # pragma: nocover
    parse_options_header = None
    multipart = None

router = APIRouter()

cytomine_logger = logging.getLogger("pims.cytomine")


@router.post('/upload', tags=['Import'])
async def legacy_import(
    request: Request,
    background: BackgroundTasks,
    core: Optional[str] = None,
    cytomine: Optional[str] = None,
    storage: Optional[int] = None,
    id_storage: Optional[int] = Query(None, alias='idStorage'),
    projects: Optional[str] = None,
    id_project: Optional[str] = Query(None, alias='idProject'),
    sync: Optional[bool] = False,
    keys: Optional[str] = None,
    values: Optional[str] = None,
    upload_name: str = Form(..., alias="files[].name"),
    upload_path: str = Form(..., alias="files[].path"),
    upload_size: int = Form(..., alias="files[].size"),
    config: Settings = Depends(get_settings),
):

    """
    Import a file (legacy)
    """
    core = cytomine if cytomine is not None else core
    if not core:
        raise BadRequestException(detail="core or cytomine parameter missing.")

    id_storage = id_storage if id_storage is not None else storage
    if not id_storage:
        raise BadRequestException(detail="idStorage or storage parameter missing.")

    projects_to_parse = id_project if id_project is not None else projects
    try:
        id_projects = []
        if projects_to_parse:
            projects = ensure_list(projects_to_parse.split(","))
            id_projects = [int(p) for p in projects]
    except ValueError:
        raise BadRequestException(detail="Invalid projects or idProject parameter.")

    public_key, signature = parse_authorization_header(request.headers)
    cytomine_auth = (core, config.cytomine_public_key, config.cytomine_private_key)
    with Cytomine(*cytomine_auth, configure_logging=False) as c:
        if not c.current_user:
            raise AuthenticationException("PIMS authentication to Cytomine failed.")

        this = get_this_image_server(config.pims_url)
        cyto_keys = c.get(f"userkey/{public_key}/keys.json")
        private_key = cyto_keys["privateKey"]

        if sign_token(private_key, parse_request_token(request)) != signature:
            raise AuthenticationException("Authentication to Cytomine failed")

        c.set_credentials(public_key, private_key)
        user = c.current_user
        storage = Storage().fetch(id_storage)
        if not storage:
            raise CytomineProblem(f"Storage {id_storage} not found")

        projects = ProjectCollection()
        for pid in id_projects:
            project = Project().fetch(pid)
            if not project:
                raise CytomineProblem(f"Project {pid} not found")
            projects.append(project)

        keys = keys.split(',') if keys is not None else []
        values = values.split(',') if values is not None else []
        if len(keys) != len(values):
            raise CytomineProblem(f"Keys {keys} and values {values} have varying size.")
        user_properties = zip(keys, values)

        upload_name = sanitize_filename(upload_name)
        root = UploadedFile(
            upload_name, upload_path, upload_size, "", "",
            id_projects, id_storage, user.id, this.id, UploadedFile.UPLOADED
        )

        cytomine = CytomineListener(
            cytomine_auth, root, projects=projects,
            user_properties=user_properties
        )

        if sync:
            try:
                run_import(
                    upload_path, upload_name,
                    extra_listeners=[cytomine], prefer_copy=False
                )
                root = cytomine.initial_uf.fetch()
                images = cytomine.images
                return [{
                    "status": 200,
                    "name": upload_name,
                    "size" : upload_size,
                    "uploadedFile": serialize_cytomine_model(root),
                    "images": [{
                        "image": serialize_cytomine_model(image[0]),
                        "imageInstances": serialize_cytomine_model(image[1])
                    } for image in images]
                }]
            except Exception as e:
                traceback.print_exc()
                return JSONResponse(
                    content=[{
                        "status": 500,
                        "error": str(e),
                        "files": [{
                            "name": upload_name,
                            "size": 0,
                            "error": str(e)
                        }]
                    }], status_code=400
                )
        else:
            send_task(
                Task.IMPORT_WITH_CYTOMINE,
                args=[cytomine_auth, upload_path, upload_name, cytomine, False],
                starlette_background=background
            )

            return JSONResponse(
                content=[{
                    "status": 200,
                    "name": upload_name,
                    "size" : upload_size,
                    "uploadedFile": serialize_cytomine_model(root),
                    "images": []
                }], status_code=200
            )
        
@router.post('/upload/direct', tags=['Import'])
async def import_direct(
    background: BackgroundTasks,
    storage: Optional[int] = None,
    id_storage: Optional[int] = Query(None, alias='idStorage'),
    projects: Optional[str] = None,
    id_project: Optional[str] = Query(None, alias='idProject'),
    sync: Optional[bool] = False,
    file: UploadFile = File(...,alias="files[].file")
):

    """
    Import a file (legacy)
    """
    PENDING_PATH = get_settings().pending_path

    id_storage = id_storage if id_storage is not None else storage
    if not id_storage:
        raise BadRequestException(detail="idStorage or storage parameter missing.")

    projects_to_parse = id_project if id_project is not None else projects
    try:
        id_projects = []
        if projects_to_parse:
            projects = ensure_list(projects_to_parse.split(","))
            id_projects = [int(p) for p in projects]
    except ValueError:
        raise BadRequestException(detail="Invalid projects or idProject parameter.")
    
    name = sanitize_filename(file.filename)
    filepath = PENDING_PATH
    file_content = await file.read()
    pending_path = Path(filepath,name)

    if not os.path.exists(pending_path.parent):
        os.makedirs(pending_path.parent)

    with open(pending_path, "wb") as f:
        f.write(file_content)

    if sync:
        try:
            run_import(
                None, pending_path, name, None, prefer_copy=False
            )
            return [{
                "status": 200,
                "name": name,
                "size" : len(file_content)
            }]
        except Exception as e:
            traceback.print_exc()
            return JSONResponse(
                content=[{
                    "status": 500,
                    "error": str(e),
                    "files": [{
                        "name": name,
                        "size": 0,
                        "error": str(e)
                    }]
                }], status_code=400
            )
    else:
        send_task(
            Task.IMPORT_WITH_CYTOMINE,
            args=[None, pending_path, name, None, False],
            starlette_background=background
        )

    return JSONResponse(
        content=[{
            "status": 200,
            "name": name,
            "size" : len(file_content)
        }], status_code=200
    )

@router.post('/upload/direct-chunks', tags=['Import'])
async def import_direct_chunks(
    background: BackgroundTasks,
    request: Request, 
    storage: Optional[int] = None,
    id_storage: Optional[int] = Query(None, alias='idStorage'),
    projects: Optional[str] = None,
    id_project: Optional[str] = Query(None, alias='idProject'),
    sync: Optional[bool] = False
):

    """
    Import a file (legacy)
    """
    PENDING_PATH = get_settings().pending_path
    filename_pattern = r'filename="([^"]+)"'

    id_storage = id_storage if id_storage is not None else storage
    if not id_storage:
        raise BadRequestException(detail="idStorage or storage parameter missing.")

    projects_to_parse = id_project if id_project is not None else projects
    try:
        id_projects = []
        if projects_to_parse:
            projects = ensure_list(projects_to_parse.split(","))
            id_projects = [int(p) for p in projects]
    except ValueError:
        raise BadRequestException(detail="Invalid projects or idProject parameter.")
    
    # first_chunk = True
    upload_path = PENDING_PATH
    # name = "tmp-name"
    # pending_path = Path(upload_path,name)

    multipart_parser = MultiPartParser(request.headers, request.stream())
    pending_path , name = await write_file(multipart_parser,upload_path)


    # async with aiofiles.open(pending_path, 'wb') as f:
    #     async for chunk in request.stream():
    #         if first_chunk:
    #             empty_line_index = chunk.find(b'\r\n\r\n')
    #             if empty_line_index != -1:
    #                 header_chunk = chunk[:empty_line_index].decode()
    #                 match = re.search(filename_pattern, header_chunk)
    #                 if match:
    #                     name = match.group(1)
    #                 else:
    #                     name = 'no-name'
    #                 chunk = chunk[empty_line_index + 4:]
    #                 first_chunk=False
    #         await f.write(chunk.strip())

    # os.rename(pending_path, Path(upload_path,name))
    # pending_path = Path(upload_path,name)

    if sync:
        try:
            run_import(
                None, pending_path, name, None, False
            )
            return [{
                "status": 200,
                "name": name,
                "size": request.headers['content-length'],
            }]
        except Exception as e:
            traceback.print_exc()
            return JSONResponse(
                content=[{
                    "status": 500,
                    "error": str(e),
                    "files": [{
                        "size": 0,
                        "error": str(e)
                    }]
                }], status_code=400
            )
    else:
        send_task(
            Task.IMPORT_WITH_CYTOMINE,
            args=[None, pending_path, name, None, False],
            starlette_background=background
        )

    return JSONResponse(
        content=[{
            "status": 200,
            "name": name,
            "size": request.headers['content-length'],
        }], status_code=200
    )


def import_(filepath, body):
    pass


@router.get('/file/{filepath:path}/export', tags=['Export'])
def export_file(
    background: BackgroundTasks,
    path: Path = Depends(filepath_parameter)
):
    """
    Export a file. All files with an identified PIMS role in the server base path can be exported.
    """
    if not (path.has_upload_role() or path.has_original_role() or path.has_spatial_role() or path.has_spectral_role()):
        raise BadRequestException()

    if path.is_dir():
        tmp_export = Path(f"/tmp/{unique_name_generator()}")
        make_zip_archive(tmp_export, path)

        def cleanup(tmp):
            tmp.unlink(missing_ok=True)

        background.add_task(cleanup, tmp_export)
        exported = tmp_export
    else:
        exported = path

    return FileResponse(
        exported,
        media_type="application/octet-stream",
        filename=path.name
    )


@router.get('/image/{filepath:path}/export', tags=['Export'])
def export_upload(
    background: BackgroundTasks,
    path: Path = Depends(imagepath_parameter),
):
    """
    Export the upload representation of an image.
    """
    image = path.get_original()
    check_representation_existence(image)

    upload_file = image.get_upload().resolve()
    media_type = image.media_type
    if upload_file.is_dir():
        # if archive has been deleted
        tmp_export = Path(f"/tmp/{unique_name_generator()}")
        make_zip_archive(tmp_export, upload_file)

        def cleanup(tmp):
            tmp.unlink(missing_ok=True)

        background.add_task(cleanup, tmp_export)
        upload_file = tmp_export
        media_type = "application/zip"

    return FileResponse(
        upload_file,
        media_type=media_type,
        filename=upload_file.name
    )


def delete(filepath):
    pass

async def write_file(fastapi_parser: MultiPartParser, upload_path):
    _, params = parse_options_header(fastapi_parser.headers["Content-Type"])
    charset = params.get(b"charset", "utf-8")
    if type(charset) == bytes:
        charset = charset.decode("latin-1")
    fastapi_parser._charset = charset
    filename = 'no-name'

    header_field = b""
    header_value = b""
    content_disposition = None
    content_type = b""
    field_name = ""
    data = b""
    pending_path = Path(upload_path,filename)

    boundary = params[b"boundary"]
    callbacks = {
            "on_part_begin": fastapi_parser.on_part_begin,
            "on_part_data": fastapi_parser.on_part_data,
            "on_part_end": fastapi_parser.on_part_end,
            "on_header_field": fastapi_parser.on_header_field,
            "on_header_value": fastapi_parser.on_header_value,
            "on_header_end": fastapi_parser.on_header_end,
            "on_headers_finished": fastapi_parser.on_headers_finished,
            "on_end": fastapi_parser.on_end,
        }
    parser = multipart.MultipartParser(boundary,callbacks)
    async with aiofiles.open(pending_path, 'wb') as f:
        async for chunk in fastapi_parser.stream:
                    parser.write(chunk)
                    messages = list(fastapi_parser.messages)
                    fastapi_parser.messages.clear()
                    for message_type, message_bytes in messages:
                        if message_type == MultiPartMessage.PART_BEGIN:
                            content_disposition = None
                            content_type = b""
                            data = b""
                        elif message_type == MultiPartMessage.HEADER_FIELD:
                            header_field += message_bytes
                        elif message_type == MultiPartMessage.HEADER_VALUE:
                            header_value += message_bytes
                        elif message_type == MultiPartMessage.HEADER_END:
                            field = header_field.lower()
                            if field == b"content-disposition":
                                content_disposition = header_value
                            elif field == b"content-type":
                                content_type = header_value
                            header_field = b""
                            header_value = b""
                        elif message_type == MultiPartMessage.HEADERS_FINISHED:
                            disposition, options = parse_options_header(content_disposition)
                            field_name = _user_safe_decode(options[b"name"], charset)
                            if b"filename" in options:
                                filename = _user_safe_decode(options[b"filename"], charset)
                        elif message_type == MultiPartMessage.PART_DATA:
                                await f.write(message_bytes)
                        elif message_type == MultiPartMessage.PART_END:
                            pass
                        elif message_type == MultiPartMessage.END:
                            pass

    os.rename(pending_path, Path(upload_path,filename))
    return Path(upload_path,filename), filename

