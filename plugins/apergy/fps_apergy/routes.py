import json
import pathlib
import sys
import uuid
from http import HTTPStatus

from fastapi import APIRouter, Depends, Response
from fastapi.responses import FileResponse
from fps.hooks import register_router  # type: ignore
from fps_auth.backends import current_user  # type: ignore
from fps_auth.backends import websocket_for_current_user  # type: ignore
from fps_auth.models import UserRead  # type: ignore
from fps_lab.config import get_lab_config  # type: ignore
from fps_yjs.routes import YDocWebSocketHandler  # type: ignore
from starlette.requests import Request  # type: ignore

from fps_kernels.kernel_driver.driver import KernelDriver  # type: ignore
from fps_kernels.kernel_server.server import (  # type: ignore
    AcceptedWebSocket,
    KernelServer,
    kernels,
)
from .models import Execution, ApergyExecution

router = APIRouter()

kernelspecs: dict = {}
sessions: dict = {}
prefix_dir: pathlib.Path = pathlib.Path(sys.prefix)


@router.post("/api/apergy/{kernel_id}/execute/")
async def execute_cell(
    request: Request,
    kernel_id,
    user: UserRead = Depends(current_user("kernels")),
):
    r = await request.json()
    execution = Execution(**r)
    if kernel_id in kernels:
        ynotebook = YDocWebSocketHandler.websocket_server.get_room(
            execution.document_id).document
        cell = ynotebook.get_cell(execution.cell_idx)
        cell["outputs"] = []

        kernel = kernels[kernel_id]
        kernelspec_path = str(
            prefix_dir / "share" / "jupyter" /
            "kernels" / kernel["name"] / "kernel.json"
        )
        if not kernel["driver"]:
            kernel["driver"] = driver = KernelDriver(
                kernelspec_path=kernelspec_path,
                write_connection_file=False,
                connection_file=kernel["server"].connection_file_path,
            )
            await driver.connect()
        driver = kernel["driver"]

        await driver.execute(cell)
        ynotebook.set_cell(execution.cell_idx, cell)


async def create_kernel():
    # Create a Temporary Kernel
    kernelspec_path = str(
        prefix_dir / "share" / "jupyter" / "kernels" / "python3" / "kernel.json"
    )
    kernel_server = KernelServer(kernelspec_path=kernelspec_path)
    await kernel_server.start()
    driver = KernelDriver(
        kernelspec_path=kernelspec_path,
        write_connection_file=False,
        connection_file=kernel_server.connection_file_path,
    )
    await driver.connect()
    kernel_id = str(uuid.uuid4())
    kernels[kernel_id] = {"name": "python3",
                          "server": kernel_server, "driver": driver}
    return kernels[kernel_id]


@router.post("/api/apergy/execute")
async def apergy_execute(
    request: Request,
    user: UserRead = Depends(current_user("kernels")),
):
    r = await request.json()
    execution = ApergyExecution(**r)

    kernel = await create_kernel()

    ynotebook = YDocWebSocketHandler.websocket_server.get_room(
        execution.notebook_id).document

    # execute a single cell
    if execution.mode == "single":
        cells = ynotebook._ycells
        index = next(i for i, x in enumerate(cells) if ynotebook.get_cell(i)["id"] == execution.cell_id)
        cell = ynotebook.get_cell(index)
        cell["outputs"] = []
        await kernel["driver"].execute(cell)
        ynotebook.set_cell(index, cell)

    # execute until cell
    if execution.mode == "until":
        cells = ynotebook._ycells
        index = next(i for i, x in enumerate(cells) if ynotebook.get_cell(i)["id"] == execution.cell_id)
        for idx in range(index+1):
            cell = ynotebook.get_cell(idx)
            cell["outputs"] = []
            await kernel["driver"].execute(cell)
            ynotebook.set_cell(idx, cell)

    # execute from cell
    if execution.mode == "from":
        cells = ynotebook._ycells
        index = next(i for i, x in enumerate(cells) if ynotebook.get_cell(i)["id"] == execution.cell_id)
        for idx in range(index, len(cells)+1):
            cell = ynotebook.get_cell(idx)
            cell["outputs"] = []
            await kernel["driver"].execute(cell)
            ynotebook.set_cell(idx, cell)

    # kill temporary kernel
    await kernel["server"].stop()

r = register_router(router)
