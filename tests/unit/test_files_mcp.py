"""Unit tests for the Phase 3 in-house files MCP server (mcp/files/server.py)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import anyio
import pytest
from control_plane.contracts import ErrorCode
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.server.mcpserver.exceptions import ToolError

from mcp import ClientSession

REPO_ROOT = Path(__file__).resolve().parents[2]
SERVER_PATH = REPO_ROOT / "mcp" / "files" / "server.py"


def _load_files_server_module():
    """Load mcp/files/server.py without shadowing the installed 'mcp' SDK."""
    spec = importlib.util.spec_from_file_location("overwatch_files_server", SERVER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["overwatch_files_server"] = module
    spec.loader.exec_module(module)
    return module


files_mod = _load_files_server_module()
FilesServerConfig = files_mod.FilesServerConfig
FilesServerError = files_mod.FilesServerError
FilesListResult = files_mod.FilesListResult
FilesReadResult = files_mod.FilesReadResult
FilesWriteResult = files_mod.FilesWriteResult
FilesDeleteResult = files_mod.FilesDeleteResult
create_files_server = files_mod.create_files_server
execute_files_list = files_mod.execute_files_list
execute_files_read = files_mod.execute_files_read
execute_files_write = files_mod.execute_files_write
execute_files_delete = files_mod.execute_files_delete


@pytest.fixture
def workspace_dir(tmp_path: Path) -> Path:
    """Create a seeded isolated workspace directory for files MCP tests."""
    ws = tmp_path / "workspace"
    ws.mkdir()
    (ws / "hello.txt").write_text("hello world\n", encoding="utf-8")
    src = ws / "src"
    src.mkdir()
    (src / "app.py").write_text("print('overwatch')\n", encoding="utf-8")
    return ws


# ---------------------------------------------------------------------------
# 1. Contract CRUD Operations & Exact Return Shapes
# ---------------------------------------------------------------------------
def test_files_list_read_write_delete_round_trip(workspace_dir: Path) -> None:
    """list/read/write/delete operate strictly inside workspace with exact schemas."""
    cfg = FilesServerConfig(workspace_root=workspace_dir)

    # list root
    listed = execute_files_list(path=".", config=cfg)
    assert isinstance(listed, FilesListResult)
    dumped_list = listed.model_dump()
    assert set(dumped_list.keys()) == {"entries"}
    names = [e["name"] for e in dumped_list["entries"]]
    assert names == ["hello.txt", "src"]
    types = {e["name"]: e["type"] for e in dumped_list["entries"]}
    assert types == {"hello.txt": "file", "src": "dir"}
    for entry in dumped_list["entries"]:
        assert set(entry.keys()) == {"name", "type", "size"}
        assert isinstance(entry["size"], int)

    # write nested file
    written = execute_files_write(
        path="nested/dir/notes.txt", content="alpha β", config=cfg
    )
    assert isinstance(written, FilesWriteResult)
    assert written.model_dump() == {"bytes": len("alpha β".encode("utf-8"))}

    # read back full and bounded
    read_full = execute_files_read(path="nested/dir/notes.txt", config=cfg)
    assert isinstance(read_full, FilesReadResult)
    assert read_full.model_dump() == {"content": "alpha β"}

    read_capped = execute_files_read(path="hello.txt", max_bytes=5, config=cfg)
    assert read_capped.model_dump() == {"content": "hello"}

    # delete existing file, then delete nonexistent returns deleted=False
    del_ok = execute_files_delete(path="nested/dir/notes.txt", config=cfg)
    assert isinstance(del_ok, FilesDeleteResult)
    assert del_ok.model_dump() == {"deleted": True}

    del_missing = execute_files_delete(path="nested/dir/notes.txt", config=cfg)
    assert del_missing.model_dump() == {"deleted": False}


# ---------------------------------------------------------------------------
# 2. PathJail Security Enforcement & Host Path Protection
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "bad_path",
    [
        "../etc/passwd",
        "../../etc/shadow",
        "src/../../etc/passwd",
        "/etc/passwd",
        "/workspace/hello.txt",
        "bad\x00path.txt",
        "\uff0e\uff0e/etc/passwd",
    ],
)
def test_path_jail_rejects_traversal_absolute_and_null_bytes(
    workspace_dir: Path, bad_path: str
) -> None:
    """All 4 file tools reject path escapes with ErrorCode.PATH_JAIL."""
    cfg = FilesServerConfig(workspace_root=workspace_dir)

    with pytest.raises(FilesServerError) as exc_list:
        execute_files_list(path=bad_path, config=cfg)
    assert exc_list.value.code == ErrorCode.PATH_JAIL
    assert str(workspace_dir) not in str(exc_list.value)

    with pytest.raises(FilesServerError) as exc_read:
        execute_files_read(path=bad_path, config=cfg)
    assert exc_read.value.code == ErrorCode.PATH_JAIL
    assert str(workspace_dir) not in str(exc_read.value)

    with pytest.raises(FilesServerError) as exc_write:
        execute_files_write(path=bad_path, content="x", config=cfg)
    assert exc_write.value.code == ErrorCode.PATH_JAIL
    assert str(workspace_dir) not in str(exc_write.value)

    with pytest.raises(FilesServerError) as exc_del:
        execute_files_delete(path=bad_path, config=cfg)
    assert exc_del.value.code == ErrorCode.PATH_JAIL
    assert str(workspace_dir) not in str(exc_del.value)


def test_symlink_escapes_rejected_and_internal_symlinks_safe(
    workspace_dir: Path, tmp_path: Path
) -> None:
    """Symlinks escaping workspace fail closed; symlinks inside workspace stay safe."""
    outside = tmp_path / "outside_secret"
    outside.mkdir()
    (outside / "secret.txt").write_text("TOP_SECRET", encoding="utf-8")

    (workspace_dir / "out_dir").symlink_to(outside)
    (workspace_dir / "out_file").symlink_to(outside / "secret.txt")
    (workspace_dir / "in_link.txt").symlink_to("hello.txt")

    cfg = FilesServerConfig(workspace_root=workspace_dir)

    for escaped in ("out_dir", "out_dir/secret.txt", "out_dir/new.txt", "out_file"):
        with pytest.raises(FilesServerError) as exc_read:
            execute_files_read(path=escaped, config=cfg)
        assert exc_read.value.code == ErrorCode.PATH_JAIL

        with pytest.raises(FilesServerError) as exc_write:
            execute_files_write(path=escaped, content="overwrite", config=cfg)
        assert exc_write.value.code == ErrorCode.PATH_JAIL

        with pytest.raises(FilesServerError) as exc_del:
            execute_files_delete(path=escaped, config=cfg)
        assert exc_del.value.code == ErrorCode.PATH_JAIL

    # files.list reports symlink entry safely without dereferencing host target
    listed = execute_files_list(path=".", config=cfg)
    by_name = {e.name: e.type for e in listed.entries}
    assert by_name["out_file"] == "symlink"

    # Internal symlink inside workspace resolves safely inside workspace
    internal_read = execute_files_read(path="in_link.txt", config=cfg)
    assert internal_read.content == "hello world\n"


def test_delete_workspace_root_and_non_empty_dir_rejected(workspace_dir: Path) -> None:
    """Cannot delete workspace root '.' or non-empty directories."""
    cfg = FilesServerConfig(workspace_root=workspace_dir)

    with pytest.raises(FilesServerError) as exc_root:
        execute_files_delete(path=".", config=cfg)
    assert exc_root.value.code == ErrorCode.PATH_JAIL

    with pytest.raises(FilesServerError) as exc_dir:
        execute_files_delete(path="src", config=cfg)
    assert exc_dir.value.code == ErrorCode.ARG_INVALID


# ---------------------------------------------------------------------------
# 3. Argument Limits & Output Caps
# ---------------------------------------------------------------------------
def test_read_output_cap_and_write_max_arg_bytes_enforced(
    workspace_dir: Path,
) -> None:
    """files.read honors output_cap_bytes; files.write honors max_arg_bytes."""
    cfg = FilesServerConfig(
        workspace_root=workspace_dir,
        output_cap_bytes=10,
        write_max_arg_bytes=64,
    )

    execute_files_write(path="ok.txt", content="abcdefghijklmno", config=cfg)
    read_res = execute_files_read(path="ok.txt", max_bytes=100, config=cfg)
    assert len(read_res.content.encode("utf-8")) == 10
    assert read_res.content == "abcdefghij"

    with pytest.raises(FilesServerError) as exc_oversize:
        execute_files_write(path="big.txt", content="x" * 100, config=cfg)
    assert exc_oversize.value.code == ErrorCode.ARG_INVALID

    for bad_max in [0, -1, True]:
        with pytest.raises(FilesServerError) as exc_arg:
            execute_files_read(path="ok.txt", max_bytes=bad_max, config=cfg)
        assert exc_arg.value.code == ErrorCode.ARG_INVALID


# ---------------------------------------------------------------------------
# 4. MCP SDK Tool Schemas, Direct Calls, & Real Stdio Subprocess Entry Point
# ---------------------------------------------------------------------------
def test_mcp_sdk_tool_schemas_and_calls(workspace_dir: Path) -> None:
    """MCPServer registers files.list/read/write/delete with exact schemas."""
    cfg = FilesServerConfig(workspace_root=workspace_dir)
    server = create_files_server(config=cfg)

    async def _exercise() -> None:
        tools = await server.list_tools()
        by_name = {t.name: t for t in tools}
        assert set(by_name.keys()) == {
            "files.list",
            "files.read",
            "files.write",
            "files.delete",
        }

        assert by_name["files.list"].input_schema["required"] == ["path"]
        assert set(by_name["files.list"].input_schema["properties"].keys()) == {"path"}
        assert set(by_name["files.list"].output_schema["properties"].keys()) == {
            "entries"
        }

        assert by_name["files.read"].input_schema["required"] == ["path"]
        assert set(by_name["files.read"].input_schema["properties"].keys()) == {
            "path",
            "max_bytes",
        }
        assert set(by_name["files.read"].output_schema["properties"].keys()) == {
            "content"
        }

        assert by_name["files.write"].input_schema["required"] == ["path", "content"]
        assert set(by_name["files.write"].input_schema["properties"].keys()) == {
            "path",
            "content",
        }
        assert set(by_name["files.write"].output_schema["properties"].keys()) == {
            "bytes"
        }

        assert by_name["files.delete"].input_schema["required"] == ["path"]
        assert set(by_name["files.delete"].input_schema["properties"].keys()) == {
            "path"
        }
        assert set(by_name["files.delete"].output_schema["properties"].keys()) == {
            "deleted"
        }

        write_res = await server.call_tool(
            "files.write", {"path": "demo.txt", "content": "sdk_ok"}
        )
        assert write_res.is_error is False
        assert write_res.structured_content == {"bytes": 6}

        read_res = await server.call_tool("files.read", {"path": "demo.txt"})
        assert read_res.structured_content == {"content": "sdk_ok"}

        list_res = await server.call_tool("files.list", {"path": "."})
        assert any(
            e["name"] == "demo.txt" for e in list_res.structured_content["entries"]
        )

        del_res = await server.call_tool("files.delete", {"path": "demo.txt"})
        assert del_res.structured_content == {"deleted": True}

        with pytest.raises(ToolError) as err_exc:
            await server.call_tool("files.read", {"path": "../etc/passwd"})
        assert ErrorCode.PATH_JAIL in str(err_exc.value)

    anyio.run(_exercise)


def test_stdio_entrypoint_runs_without_sdk_shadowing(workspace_dir: Path) -> None:
    """Running mcp/files/server.py directly over stdio works cleanly with MCP SDK."""
    params = StdioServerParameters(
        command=sys.executable,
        args=[str(SERVER_PATH), "--workspace-root", str(workspace_dir)],
        cwd=str(REPO_ROOT),
    )

    async def _exercise_stdio() -> None:
        async with stdio_client(params) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                tools_resp = await session.list_tools()
                names = {t.name for t in tools_resp.tools}
                assert names == {
                    "files.list",
                    "files.read",
                    "files.write",
                    "files.delete",
                }
                res = await session.call_tool("files.read", {"path": "hello.txt"})
                assert res.is_error is False
                assert res.structured_content == {"content": "hello world\n"}

    anyio.run(_exercise_stdio)
