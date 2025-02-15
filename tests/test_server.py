import asyncio
import json
import sys

import pytest
import requests
import y_py as Y
from websockets import connect
from ypy_websocket import WebsocketProvider

prev_theme = {}
test_theme = {"raw": '{// jupyverse test\n"theme": "JupyterLab Dark"}'}


@pytest.mark.parametrize("auth_mode", ("noauth",))
@pytest.mark.parametrize("clear_users", (False,))
def test_settings_persistence_put(start_jupyverse):
    url = start_jupyverse
    # get previous theme
    response = requests.get(url + "/lab/api/settings/@jupyterlab/apputils-extension:themes")
    assert response.status_code == 200
    prev_theme["raw"] = json.loads(response.content)["raw"]
    # put new theme
    response = requests.put(
        url + "/lab/api/settings/@jupyterlab/apputils-extension:themes", data=json.dumps(test_theme)
    )
    assert response.status_code == 204


@pytest.mark.parametrize("auth_mode", ("noauth",))
@pytest.mark.parametrize("clear_users", (False,))
def test_settings_persistence_get(start_jupyverse):
    url = start_jupyverse
    # get new theme
    response = requests.get(
        url + "/lab/api/settings/@jupyterlab/apputils-extension:themes",
    )
    assert response.status_code == 200
    assert json.loads(response.content)["raw"] == test_theme["raw"]
    # put previous theme back
    response = requests.put(
        url + "/lab/api/settings/@jupyterlab/apputils-extension:themes",
        data=json.dumps(prev_theme),
    )
    assert response.status_code == 204


@pytest.mark.asyncio
@pytest.mark.parametrize("auth_mode", ("noauth",))
@pytest.mark.parametrize("clear_users", (False,))
@pytest.mark.skipif(
    sys.platform.startswith("win"),
    reason="Issue with POST /api/sessions",
)
async def test_rest_api(start_jupyverse):
    url = start_jupyverse
    ws_url = url.replace("http", "ws", 1)
    # create a session to launch a kernel
    response = requests.post(
        f"{url}/api/sessions",
        data=json.dumps(
            {
                "kernel": {"name": "python3"},
                "name": "notebook0.ipynb",
                "path": "69e8a762-86c6-4102-a3da-a43d735fec2b",
                "type": "notebook",
            }
        ),
    )
    r = response.json()
    kernel_id = r["kernel"]["id"]
    document_id = "json:notebook:tests/data/notebook0.ipynb"
    async with connect(f"{ws_url}/api/yjs/{document_id}") as websocket:
        # connect to the shared notebook document
        ydoc = Y.YDoc()
        WebsocketProvider(ydoc, websocket)
        # wait for file to be loaded and Y model to be created in server and client
        await asyncio.sleep(0.1)
        # execute notebook
        for cell_idx in range(3):
            response = requests.post(
                f"{url}/api/kernels/{kernel_id}/execute",
                data=json.dumps(
                    {
                        "document_id": document_id,
                        "cell_idx": cell_idx,
                    }
                ),
            )
        # wait for Y model to be updated
        await asyncio.sleep(0.1)
        # retrieve cells
        cells = ydoc.get_array("cells").to_json()
        assert cells[0]["outputs"] == [
            {
                "data": {"text/plain": ["3"]},
                "execution_count": 1.0,
                "metadata": {},
                "output_type": "execute_result",
            }
        ]
        assert cells[1]["outputs"] == [
            {"name": "stdout", "output_type": "stream", "text": ["Hello World!\n"]}
        ]
        assert cells[2]["outputs"] == [
            {
                "data": {"text/plain": ["7"]},
                "execution_count": 3.0,
                "metadata": {},
                "output_type": "execute_result",
            }
        ]
