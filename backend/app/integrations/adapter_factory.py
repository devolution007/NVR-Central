from collections.abc import Callable, Iterator
from contextlib import contextmanager

from app.core.security import decrypt_secret
from app.integrations.base import ConnectionParams, NvrAdapter, NvrError
from app.integrations.dahua.direct import DahuaDirectAdapter
from app.integrations.dahua.p2p import DahuaP2pAdapter
from app.integrations.hostguard import assert_host_allowed
from app.models.enums import ConnectionType
from app.models.nvr import Nvr

AdapterBuilder = Callable[[Nvr, ConnectionParams], NvrAdapter]

_override: AdapterBuilder | None = None


@contextmanager
def override_adapter(builder: AdapterBuilder) -> Iterator[None]:
    """Tests only: substitute a simulated adapter. Real runs never set this."""
    global _override
    previous, _override = _override, builder
    try:
        yield
    finally:
        _override = previous


def build_params(nvr: Nvr) -> ConnectionParams:
    if not nvr.host:
        raise NvrError("This NVR has no address configured")
    return ConnectionParams(
        host=nvr.host,
        http_port=nvr.http_port,
        rtsp_port=nvr.rtsp_port,
        username=nvr.username,
        password=decrypt_secret(nvr.password_encrypted),
    )


def get_adapter(nvr: Nvr) -> NvrAdapter:
    if nvr.connection_type == ConnectionType.P2P:
        return DahuaP2pAdapter()
    params = build_params(nvr)
    assert_host_allowed(params.host)
    if _override is not None:
        return _override(nvr, params)
    return DahuaDirectAdapter(params)
