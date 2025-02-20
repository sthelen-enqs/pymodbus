"""Test client/server stop/start."""
import asyncio
import logging
import os
from threading import Thread
from time import sleep

import pytest

from pymodbus import client, pymodbus_apply_logging_config, server
from pymodbus.datastore import (
    ModbusSequentialDataBlock,
    ModbusServerContext,
    ModbusSlaveContext,
)
from pymodbus.exceptions import ConnectionException
from pymodbus.transaction import (
    ModbusRtuFramer,
    ModbusSocketFramer,
    ModbusTlsFramer,
)


_logger = logging.getLogger()
pymodbus_apply_logging_config(logging.DEBUG)
_logger.setLevel(logging.DEBUG)

TEST_TYPES = ["tcp", "udp", "serial", "tls"]


def helper_config(request, def_type):
    """Do setup of single test-"""
    pymodbus_apply_logging_config()
    _logger.setLevel("DEBUG")
    datablock = ModbusSequentialDataBlock(0x00, [17] * 100)
    context = ModbusServerContext(
        slaves=ModbusSlaveContext(
            di=datablock, co=datablock, hr=datablock, ir=datablock, unit=1
        ),
        single=True,
    )
    cwd = os.getcwd().split("/")[-1]
    path = "../examples" if cwd == "test" else "examples"
    cfg = {
        "serial": {
            "srv_args": {
                "context": context,
                "framer": ModbusRtuFramer,
                "port": "socket://127.0.0.1:5020",
            },
            "cli_args": {
                "framer": ModbusRtuFramer,
                "port": "socket://127.0.0.1:5020",
                "timeout": 0.2,
            },
            "async": {
                "srv": server.StartAsyncSerialServer,
                "cli": client.AsyncModbusSerialClient,
            },
            "sync": {
                "srv": server.StartSerialServer,
                "cli": client.ModbusSerialClient,
            },
        },
        "tcp": {
            "srv_args": {
                "context": context,
                "framer": ModbusSocketFramer,
                "address": ("127.0.0.1", 5020),
                "allow_reuse_address": True,
            },
            "cli_args": {
                "framer": ModbusSocketFramer,
                "host": "127.0.0.1",
                "port": 5020,
                "timeout": 0.2,
            },
            "async": {
                "srv": server.StartAsyncTcpServer,
                "cli": client.AsyncModbusTcpClient,
            },
            "sync": {
                "srv": server.StartTcpServer,
                "cli": client.ModbusTcpClient,
            },
        },
        "tls": {
            "srv_args": {
                "context": context,
                "framer": ModbusTlsFramer,
                "address": ("127.0.0.1", 5020),
                "allow_reuse_address": True,
                "certfile": f"{path}/certificates/pymodbus.crt",
                "keyfile": f"{path}/certificates/pymodbus.key",
            },
            "cli_args": {
                "framer": ModbusTlsFramer,
                "host": "127.0.0.1",
                "port": 5020,
                "certfile": f"{path}/certificates/pymodbus.crt",
                "keyfile": f"{path}/certificates/pymodbus.key",
                "server_hostname": "localhost",
                "timeout": 0.2,
            },
            "async": {
                "srv": server.StartAsyncTlsServer,
                "cli": client.AsyncModbusTlsClient,
            },
            "sync": {
                "srv": server.StartTlsServer,
                "cli": client.ModbusTlsClient,
            },
        },
        "udp": {
            "srv_args": {
                "context": context,
                "framer": ModbusSocketFramer,
                "address": ("127.0.0.1", 5020),
            },
            "cli_args": {
                "framer": ModbusSocketFramer,
                "host": "127.0.0.1",
                "port": 5020,
                "timeout": 0.2,
            },
            "async": {
                "srv": server.StartAsyncUdpServer,
                "cli": client.AsyncModbusUdpClient,
            },
            "sync": {
                "srv": server.StartUdpServer,
                "cli": client.ModbusUdpClient,
            },
        },
    }

    cur = cfg[request]
    cur_m = cur[def_type]
    return cur_m["srv"], cur["srv_args"], cur_m["cli"], cur["cli_args"]


@pytest.mark.xdist_group(name="task_serialize")
@pytest.mark.parametrize("comm", TEST_TYPES)
async def test_async_task_no_server(comm):
    """Test normal client/server handling."""
    run_server, server_args, run_client, client_args = helper_config(comm, "async")
    client = run_client(**client_args)
    try:
        await client.connect()
    except Exception as exc:  # pylint: disable=broad-except
        assert False, f"unexpected exception: {exc}"
    await asyncio.sleep(0.1)
    with pytest.raises((asyncio.exceptions.TimeoutError, ConnectionException)):
        await client.read_coils(1, 1, slave=0x01)
    await client.close()


@pytest.mark.xdist_group(name="task_serialize")
@pytest.mark.parametrize("comm", TEST_TYPES)
async def test_async_task_ok(comm):
    """Test normal client/server handling."""
    run_server, server_args, run_client, client_args = helper_config(comm, "async")

    task = asyncio.create_task(run_server(**server_args))
    await asyncio.sleep(0.1)
    client = run_client(**client_args)
    await client.connect()
    await asyncio.sleep(0.1)
    assert client.protocol
    rr = await client.read_coils(1, 1, slave=0x01)
    assert len(rr.bits) == 8

    await client.close()
    await asyncio.sleep(0.1)
    assert not client.protocol
    await server.ServerAsyncStop()
    await task


@pytest.mark.xdist_group(name="task_serialize")
@pytest.mark.parametrize("comm", TEST_TYPES)
async def test_async_task_server_stop(comm):
    """Test normal client/server handling."""
    run_server, server_args, run_client, client_args = helper_config(comm, "async")
    task = asyncio.create_task(run_server(**server_args))
    await asyncio.sleep(0.1)
    client = run_client(**client_args)
    await client.connect()
    assert client.protocol
    rr = await client.read_coils(1, 1, slave=0x01)
    assert len(rr.bits) == 8

    # Server breakdown
    await server.ServerAsyncStop()
    await task

    with pytest.raises((ConnectionException, asyncio.exceptions.TimeoutError)):
        rr = await client.read_coils(1, 1, slave=0x01)
    assert not client.protocol

    # Server back online
    task = asyncio.create_task(run_server(**server_args))
    await asyncio.sleep(0.1)

    timer_allowed = 100
    while not client.protocol:
        await asyncio.sleep(0.1)
        timer_allowed -= 1
        if not timer_allowed:
            assert False, "client do not reconnect"
    assert client.protocol

    rr = await client.read_coils(1, 1, slave=0x01)
    assert len(rr.bits) == 8

    await client.close()
    await asyncio.sleep(0.5)
    assert not client.protocol
    await server.ServerAsyncStop()
    await task


@pytest.mark.xdist_group(name="task_serialize")
@pytest.mark.parametrize("comm", TEST_TYPES)
def test_sync_task_no_server(comm):
    """Test normal client/server handling."""
    if comm == "udp":
        return
    run_server, server_args, run_client, client_args = helper_config(comm, "sync")
    client = run_client(**client_args)
    try:
        client.connect()
    except Exception as exc:  # pylint: disable=broad-except
        assert False, f"unexpected exception: {exc}"
    sleep(0.1)
    with pytest.raises((asyncio.exceptions.TimeoutError, ConnectionException)):
        client.read_coils(1, 1, slave=0x01)
    client.close()


@pytest.mark.xdist_group(name="task_serialize")
@pytest.mark.parametrize("comm", TEST_TYPES)
def test_sync_task_ok(comm):
    """Test normal client/server handling."""
    run_server, server_args, run_client, client_args = helper_config(comm, "sync")
    if comm in {"serial", "udp", "tls"}:
        return
    thread = Thread(target=run_server, kwargs=server_args)
    thread.daemon = True
    thread.start()
    sleep(0.1)
    client = run_client(**client_args)
    client.connect()
    sleep(0.1)
    assert client.socket
    rr = client.read_coils(1, 1, slave=0x01)
    assert len(rr.bits) == 8

    client.close()
    sleep(0.1)
    assert not client.socket
    server.ServerStop()


@pytest.mark.xdist_group(name="task_serialize")
@pytest.mark.parametrize("comm", TEST_TYPES)
def test_sync_task_server_stop(comm):
    """Test normal client/server handling."""
    run_server, server_args, run_client, client_args = helper_config(comm, "sync")
    if comm:
        return  # SKIP TEST FOR NOW

    # task = asyncio.create_task(run_server(**server_args))
    # await asyncio.sleep(0.1)
    # client = run_client(**client_args)
    # await client.connect()
    # assert client.protocol
    # rr = await client.read_coils(1, 1, slave=0x01)
    # assert len(rr.bits) == 8

    # Server breakdown
    # await server.ServerAsyncStop()
    # await task

    # with pytest.raises((ConnectionException, asyncio.exceptions.TimeoutError)):
    #     rr = await client.read_coils(1, 1, slave=0x01)
    # assert not client.protocol

    # Server back online
    # task = asyncio.create_task(run_server(**server_args))
    # await asyncio.sleep(0.1)

    # timer_allowed = 100
    # while not client.protocol:
    #     await asyncio.sleep(0.1)
    #     timer_allowed -= 1
    #     if not timer_allowed:
    #         assert False, "client do not reconnect"
    # assert client.protocol

    # rr = await client.read_coils(1, 1, slave=0x01)
    # assert len(rr.bits) == 8

    # await client.close()
    # await asyncio.sleep(0.5)
    # assert not client.protocol
    # await server.ServerAsyncStop()
    # await task
