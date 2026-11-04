; The accepted layout is fixed, including silent installs. Never touch retained data.
!define MUI_CUSTOMFUNCTION_GUIINIT OversightGuiInit
Function OversightGuiInit
  StrCpy $INSTDIR "$LOCALAPPDATA\Programs\Oversight"
FunctionEnd

!macro NSIS_HOOK_PREINSTALL
  StrCpy $INSTDIR "$LOCALAPPDATA\Programs\Oversight"
  SetOutPath $INSTDIR
!macroend
