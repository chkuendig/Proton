# Opt-in imported stock closure; the default full build is unchanged.
ifneq ($(strip $(ZLUDA_STOCK_ONLY)$(ZLUDA_STOCK_REUSE)),)
ifneq ($(TARGET_ARCH),x86_64)
$(error stock closure currently supports only x86_64)
endif
ifneq ($(SOURCE_DATE_EPOCH):$(BASE_SOURCE_DATE_EPOCH),1791559305:1791559305)
$(error stock closure requires the reviewed source epochs)
endif
ifneq ($(BUILD_NAME):$(INTERNAL_TOOL_NAME),Proton-ZLUDA:Proton-ZLUDA)
$(error stock closure requires the reviewed tool identity)
endif
ifneq ($(UNSTRIPPED_BUILD),)
$(error stock closure requires the reviewed stripped install)
endif
$(foreach v,CFLAGS CXXFLAGS CPPFLAGS LDFLAGS CC CXX LD MINGW_TYPE TARGET_ARCH SUPPRESS_WARNINGS,$(if $(filter command environment override,$(origin $(v))),$(error unsupported stock configuration override: $(v))))
.PHONY: zluda-stock-configuration
zluda-stock-configuration:
	@test "$(J)" = 3 || { echo 'stock closure requires -j3' >&2; exit 1; }
all-dist: zluda-stock-configuration
endif
ifneq ($(ZLUDA_STOCK_ONLY),)
ifneq ($(ZLUDA_STOCK_ONLY),1)
$(error ZLUDA_STOCK_ONLY must be empty or 1)
endif
endif
ifneq ($(ZLUDA_STOCK_REUSE),)
ifneq ($(ZLUDA_STOCK_REUSE),1)
$(error ZLUDA_STOCK_REUSE must be empty or 1)
endif
ifneq ($(ZLUDA_STOCK_ONLY),)
$(error stock-only and stock-reuse are mutually exclusive)
endif
ifneq ($(TARGET_ARCH),x86_64)
$(error stock import currently supports only x86_64)
endif
WINE_SRC := $(OBJ)/src-wine
.PHONY: zluda-stock-ready wine
zluda-stock-ready:
	@test "$(J)" = 3 || { echo 'stock closure requires -j3' >&2; exit 1; }
	python3 "$(SRC)/make/zluda-stock.py" ready "$(SRC)" "$(OBJ)"
wine all-dist: zluda-stock-ready
endif
