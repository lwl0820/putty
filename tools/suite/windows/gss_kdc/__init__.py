"""受控真实Kerberos KDC；不是产品认证依赖。"""
from .fixture import PortableKDC, PreparedKDC

__all__ = ["PortableKDC", "PreparedKDC"]
