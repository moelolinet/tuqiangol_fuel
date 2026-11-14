"""Constants for the Tuqiangol fuel control integration."""

DOMAIN = "tuqiangol_fuel"

MANUFACTURER = "Tuqiangol"
MODEL = "途强在线定位器"

DEFAULT_BASE_URL = "https://www.tuqiangol.com"
DEFAULT_INSTRUCTION_TYPE_ID = "827"
DEFAULT_SCAN_INTERVAL = 60
DEFAULT_INVERT = False

CONF_BASE_URL = "base_url"
CONF_DEVICE_IMEI = "device_imei"
CONF_MC_TYPE = "mc_type"
CONF_SCAN_INTERVAL = "scan_interval"
CONF_INSTRUCTION_TYPE_ID = "instruction_type_id"
CONF_INSTRUCTION_PASSWORD = "instruction_password"
CONF_FUEL_PARAM_ID = "fuel_param_id"
CONF_INVERT = "invert"
CONF_DEVICE_SETTINGS = "device_settings"

STATE_CUT = "cut"
STATE_RESTORE = "restore"

FUEL_PARAM_KEYWORD_CUT = "断油"
FUEL_PARAM_KEYWORD_RESTORE = "恢复"
FUEL_RECORD_MARKER = "YL1"

# 平台返回码（/device/sendIns）
CODE_OK = 255               # 指令已下发并由终端执行
CODE_OFFLINE_SAVED = 10005  # 终端离线，指令已存入离线队列
CODE_OFFLINE_ERROR = 10006  # 终端离线且入队失败，指令未生效

# 平台回执中表示“终端已处于某状态，本次未执行、不会产生新记录”的特征串，
# 以及它们各自对应的实际状态。命中且与请求动作一致时无需等待新记录。
ALREADY_IN_STATE_CUT = "already in the cut off fuel status"
ALREADY_IN_STATE_RESTORE = "in the state of oil power connection"
ALREADY_IN_STATE_MARKERS = (
    ALREADY_IN_STATE_CUT,
    ALREADY_IN_STATE_RESTORE,
    "this instruction will not be executed",
    "this command is no longer executed",
)
