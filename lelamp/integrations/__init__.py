"""外部系统与 LeLamp runtime 的集成层。

当前模块：
- fluxchi_listener: 订阅 FluxChi backend 的 harness WebSocket，映射到 dashboard action。

所有集成默认通过 dashboard HTTP（127.0.0.1:8765）驱动硬件，不直连 service/。
详见 ../docs/EXTERNAL_INTEGRATION_CN.md。
"""
