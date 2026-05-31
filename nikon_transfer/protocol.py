"""PTP/IP protocol constants for Nikon D5300."""

PTPIP_HOST_DEFAULT = "192.168.1.1"
PTPIP_PORT         = 15740
CLIENT_NAME        = "NikonTransfer/1.0"

# PTP operation codes
OP_GET_DEVICE_INFO    = 0x1001
OP_OPEN_SESSION       = 0x1002
OP_CLOSE_SESSION      = 0x1003
OP_GET_STORAGE_IDS    = 0x1004
OP_GET_STORAGE_INFO   = 0x1005
OP_GET_NUM_OBJECTS    = 0x1006
OP_GET_OBJECT_HANDLES = 0x1007
OP_GET_OBJECT_INFO    = 0x1008
OP_GET_OBJECT         = 0x1009
OP_GET_THUMB          = 0x100A
OP_GET_PARTIAL_OBJECT = 0x101B
OP_GET_DEVICE_PROP_VALUE = 0x1015

# PTP device property codes (subset)
DPC_BATTERY_LEVEL = 0x5001
DPC_DATETIME      = 0x5011

# PTP response codes
RSP_OK                    = 0x2001
RSP_SESSION_ALREADY_OPEN  = 0x201E
RSP_INVALID_STORAGE_ID    = 0x2013
RSP_INVALID_OBJECT_HANDLE = 0x2009
RSP_INVALID_PARAMETER     = 0x201D
RSP_ACCESS_DENIED         = 0x200F

_RSP_NAMES: dict[int, str] = {
    0x2001: "OK",
    0x2002: "GeneralError",
    0x2003: "SessionNotOpen",
    0x2004: "InvalidTransactionID",
    0x2005: "OperationNotSupported",
    0x2006: "ParameterNotSupported",
    0x2007: "IncompleteTransfer",
    0x2008: "InvalidStorageType",
    0x2009: "InvalidObjectHandle",
    0x200A: "DevicePropNotSupported",
    0x200F: "AccessDenied",
    0x2013: "InvalidStorageID",
    0x201D: "InvalidParameter",
    0x201E: "SessionAlreadyOpen",
}


def rsp_name(code: int) -> str:
    if code in _RSP_NAMES:
        return _RSP_NAMES[code]
    if 0xA000 <= code <= 0xAFFF:
        return f"NikonVendor:0x{code:04X}"
    return f"0x{code:04X}"

# PTP/IP packet types
PKT_INIT_CMD_REQUEST   = 0x0001
PKT_INIT_CMD_ACK       = 0x0002
PKT_INIT_EVENT_REQUEST = 0x0003
PKT_INIT_EVENT_ACK     = 0x0004
PKT_CMD_REQUEST        = 0x0006
PKT_CMD_RESPONSE       = 0x0007
PKT_EVENT              = 0x0008
PKT_DATA_START         = 0x0009
PKT_DATA_END           = 0x000C

# PTP event codes (subset — only the ones we react to)
EVT_OBJECT_ADDED       = 0x4002
EVT_OBJECT_REMOVED     = 0x4003
EVT_STORE_FULL         = 0x400A
EVT_CAPTURE_COMPLETE   = 0x400D

IMAGE_EXTENSIONS = frozenset({".jpg", ".jpeg", ".nef", ".nrw", ".tif", ".tiff", ".png"})
