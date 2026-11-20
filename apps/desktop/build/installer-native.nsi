; AgentFlow OS - NSIS installer script (single-pass, native makensis build)
; ---------------------------------------------------------------------------
; Why this file exists:
;   electron-builder's own NSIS flow is two-pass: it compiles an uninstaller
;   carrier, then must EXECUTE it (via wine on Linux) to extract the real
;   uninstaller before compiling the final installer. In this build VM wine
;   cannot run (segfault / missing wine32), so that flow stops after pass 1
;   and leaves only a ~188 KB carrier stub.
;   This script mirrors the electron-builder configuration from package.json
;   (productName "AgentFlow OS", appId os.agentflow.app, version 0.1.0,
;   oneClick=false, allowToChangeInstallationDirectory=true,
;   createDesktopShortcut=true, per-user assisted install) but generates the
;   uninstaller at install time with WriteUninstaller, so a single native
;   makensis pass produces the complete installer. No wine involved.
; Payload: the win-unpacked directory produced by `electron-builder --win`
;   (packaging step), embedded with File /r.
; Build:  makensis -V2 installer-native.nsi   (NSIS 3.x, Linux native)
; ---------------------------------------------------------------------------

Unicode true
; Non-solid LZMA: solid mode's encoder allocation over the full
; payload (Electron runtime + embedded backend) crashes the NSIS
; compiler on this build machine; per-file LZMA with the same
; dictionary builds reliably and the size difference is small.
SetCompressor lzma
SetCompressorDictSize 64
SetDatablockOptimize on

!include "MUI2.nsh"
!include "LogicLib.nsh"
!include "FileFunc.nsh"
!include "x64.nsh"

!define PRODUCT_NAME "AgentFlow OS"
!define PRODUCT_FILENAME "AgentFlow OS"
!define APP_ID "os.agentflow.app"
!define VERSION "0.1.5"
!define UNINSTALL_FILENAME "Uninstall AgentFlow OS.exe"
!define UNINSTALL_REGISTRY_KEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_ID}"
!define INSTALL_REGISTRY_KEY "Software\${APP_ID}"

Name "${PRODUCT_NAME}"
OutFile "/home/hatch/workspace/agentflow-os/apps/desktop/release/AgentFlow OS Setup 0.1.5.exe"
InstallDir "$LOCALAPPDATA\Programs\${PRODUCT_FILENAME}"
RequestExecutionLevel user
ShowInstDetails show
ShowUninstDetails show
BrandingText "${PRODUCT_NAME} ${VERSION}"

VIProductVersion "0.1.5.0"
VIAddVersionKey "ProductName" "${PRODUCT_NAME}"
VIAddVersionKey "FileDescription" "AgentFlow OS Setup"
VIAddVersionKey "FileVersion" "${VERSION}"
VIAddVersionKey "ProductVersion" "${VERSION}"
VIAddVersionKey "LegalCopyright" ""

; --- Installer pages (assisted, mirrors oneClick=false) ---
!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!define MUI_FINISHPAGE_RUN "$INSTDIR\${PRODUCT_FILENAME}.exe"
!define MUI_FINISHPAGE_RUN_TEXT "Run ${PRODUCT_NAME}"
!insertmacro MUI_PAGE_FINISH

; --- Uninstaller pages ---
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES

!insertmacro MUI_LANGUAGE "English"
!insertmacro MUI_LANGUAGE "SimpChinese"

Function .onInit
  ; x64 only build
  ${IfNot} ${RunningX64}
    MessageBox MB_OK|MB_ICONSTOP "AgentFlow OS requires a 64-bit version of Windows."
    Abort
  ${EndIf}
  ; allow only one installer instance (same guard electron-builder applies)
  System::Call 'kernel32::CreateMutex(p 0, i 0, t "AgentFlowOSSetupMutex") p .r1 ?e'
  Pop $R0
  ${If} $R0 != "0"
    MessageBox MB_OK|MB_ICONEXCLAMATION "The AgentFlow OS installer is already running."
    Abort
  ${EndIf}
FunctionEnd

Section "install" SEC_INSTALL
  SetOutPath "$INSTDIR"

  ; --- application payload (electron win-unpacked output) ---
  File /r "/home/hatch/workspace/agentflow-os/apps/desktop/release/win-unpacked/*.*"

  ; --- uninstaller, generated at install time (single-pass pattern) ---
  WriteUninstaller "$INSTDIR\${UNINSTALL_FILENAME}"

  ; --- install info registry (mirrors electron-builder registryAddInstallInfo) ---
  WriteRegStr HKCU "${INSTALL_REGISTRY_KEY}" InstallLocation "$INSTDIR"
  WriteRegStr HKCU "${INSTALL_REGISTRY_KEY}" KeepShortcuts "true"
  WriteRegStr HKCU "${INSTALL_REGISTRY_KEY}" ShortcutName "${PRODUCT_NAME}"

  WriteRegStr HKCU "${UNINSTALL_REGISTRY_KEY}" DisplayName "${PRODUCT_NAME}"
  WriteRegStr HKCU "${UNINSTALL_REGISTRY_KEY}" UninstallString '"$INSTDIR\${UNINSTALL_FILENAME}" /currentuser'
  WriteRegStr HKCU "${UNINSTALL_REGISTRY_KEY}" QuietUninstallString '"$INSTDIR\${UNINSTALL_FILENAME}" /currentuser /S'
  WriteRegStr HKCU "${UNINSTALL_REGISTRY_KEY}" DisplayVersion "${VERSION}"
  WriteRegStr HKCU "${UNINSTALL_REGISTRY_KEY}" DisplayIcon "$INSTDIR\${PRODUCT_FILENAME}.exe,0"
  WriteRegDWORD HKCU "${UNINSTALL_REGISTRY_KEY}" NoModify 1
  WriteRegDWORD HKCU "${UNINSTALL_REGISTRY_KEY}" NoRepair 1
  ${GetSize} "$INSTDIR" "/S=0K" $0 $1 $2
  IntFmt $0 "0x%08X" $0
  WriteRegDWORD HKCU "${UNINSTALL_REGISTRY_KEY}" EstimatedSize "$0"

  ; --- shortcuts (createDesktopShortcut=true, start menu link enabled) ---
  CreateShortcut "$DESKTOP\${PRODUCT_NAME}.lnk" "$INSTDIR\${PRODUCT_FILENAME}.exe"
  CreateShortcut "$SMPROGRAMS\${PRODUCT_NAME}.lnk" "$INSTDIR\${PRODUCT_FILENAME}.exe"
SectionEnd

Section "Uninstall"
  Delete "$DESKTOP\${PRODUCT_NAME}.lnk"
  Delete "$SMPROGRAMS\${PRODUCT_NAME}.lnk"

  DeleteRegKey HKCU "${UNINSTALL_REGISTRY_KEY}"
  DeleteRegKey HKCU "${INSTALL_REGISTRY_KEY}"

  RMDir /r "$INSTDIR"
SectionEnd
