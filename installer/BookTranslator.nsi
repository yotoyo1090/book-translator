; Installateur Windows de Book Translator (NSIS 3)
; Construire : ./build.sh  (Linux, makensis)  ou  makensis BookTranslator.nsi (Windows)

Unicode true
!define APP "Book Translator"
!define VERSION "1.0.0"
!define UNINST_KEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\BookTranslator"

Name "${APP}"
OutFile "build\BookTranslator_Setup.exe"
InstallDir "$LOCALAPPDATA\BookTranslator"
RequestExecutionLevel admin          ; Tesseract s'installe dans Program Files
SetCompressor /SOLID lzma
BrandingText "${APP} ${VERSION}"

!include "MUI2.nsh"
!include "nsDialogs.nsh"
!include "LogicLib.nsh"

!define MUI_ICON "icon.ico"
!define MUI_UNICON "icon.ico"
!define MUI_ABORTWARNING
!define MUI_WELCOMEFINISHPAGE_BITMAP "welcome.bmp"
!define MUI_UNWELCOMEFINISHPAGE_BITMAP "welcome.bmp"
; langue choisie gardée pour le désinstalleur
!define MUI_LANGDLL_REGISTRY_ROOT "HKCU"
!define MUI_LANGDLL_REGISTRY_KEY "Software\BookTranslator"
!define MUI_LANGDLL_REGISTRY_VALUENAME "Language"
!define MUI_FINISHPAGE_RUN
!define MUI_FINISHPAGE_RUN_FUNCTION Lancer

!insertmacro MUI_PAGE_WELCOME
Page custom PageModele PageModeleQuitter
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES

!insertmacro MUI_LANGUAGE "Korean"
!insertmacro MUI_LANGUAGE "English"
!insertmacro MUI_LANGUAGE "French"

; ---------- textes ----------
LangString T_TITRE  ${LANG_KOREAN}  "AI 번역 모델"
LangString T_TITRE  ${LANG_ENGLISH} "AI translation model"
LangString T_TITRE  ${LANG_FRENCH}  "Modèle IA de traduction"
LangString T_SOUS   ${LANG_KOREAN}  "컴퓨터에 맞는 모델을 고르세요. 나중에 Ollama로 바꿀 수 있습니다."
LangString T_SOUS   ${LANG_ENGLISH} "Choose the model that fits your computer. You can change it later with Ollama."
LangString T_SOUS   ${LANG_FRENCH}  "Choisis le modèle adapté à ton ordinateur. Tu pourras le changer plus tard avec Ollama."
LangString T_GPU    ${LANG_KOREAN}  "감지된 그래픽 카드 메모리: $0 GB"
LangString T_GPU    ${LANG_ENGLISH} "Detected graphics card memory: $0 GB"
LangString T_GPU    ${LANG_FRENCH}  "Mémoire de carte graphique détectée : $0 Go"
LangString T_4B     ${LANG_KOREAN}  "가벼운 모델 (translategemma:4b · 3.3 GB) — 대부분의 PC, RAM 8 GB 이상"
LangString T_4B     ${LANG_ENGLISH} "Light model (translategemma:4b · 3.3 GB) — most PCs, 8 GB RAM or more"
LangString T_4B     ${LANG_FRENCH}  "Modèle léger (translategemma:4b · 3,3 Go) — la plupart des PC, 8 Go de RAM ou plus"
LangString T_12B    ${LANG_KOREAN}  "고품질 모델 (translategemma:12b · 8.1 GB) — 그래픽 카드 메모리 8 GB 이상"
LangString T_12B    ${LANG_ENGLISH} "High-quality model (translategemma:12b · 8.1 GB) — graphics card with 8 GB or more"
LangString T_12B    ${LANG_FRENCH}  "Modèle haute qualité (translategemma:12b · 8,1 Go) — carte graphique de 8 Go ou plus"
LangString T_AUCUN  ${LANG_KOREAN}  "지금은 다운로드하지 않음 (이미 모델이 있거나 나중에 받을 경우)"
LangString T_AUCUN  ${LANG_ENGLISH} "Don't download now (model already installed, or later)"
LangString T_AUCUN  ${LANG_FRENCH}  "Ne pas télécharger maintenant (modèle déjà installé, ou plus tard)"
LangString T_NOTE   ${LANG_KOREAN}  "다음 단계에서 Python, Tesseract OCR, Ollama와 모델을 인터넷에서 자동으로 받아 설치합니다. 창이 하나 열려 진행 상황을 보여줍니다."
LangString T_NOTE   ${LANG_ENGLISH} "Next, Python, Tesseract OCR, Ollama and the model are downloaded and installed automatically. A window shows the progress."
LangString T_NOTE   ${LANG_FRENCH}  "Ensuite, Python, Tesseract OCR, Ollama et le modèle sont téléchargés et installés automatiquement. Une fenêtre montre la progression."
LangString T_ECHEC  ${LANG_KOREAN}  "구성 요소 설치에 실패했습니다. 인터넷 연결을 확인하고 설치 프로그램을 다시 실행하세요 (이미 설치된 부분은 건너뜁니다)."
LangString T_ECHEC  ${LANG_ENGLISH} "Installing the components failed. Check your internet connection and run the installer again (finished parts are skipped)."
LangString T_ECHEC  ${LANG_FRENCH}  "L'installation des composants a échoué. Vérifie la connexion Internet et relance l'installateur (les parties déjà faites sont sautées)."
LangString T_COMPOS ${LANG_KOREAN}  "구성 요소 설치 중 (Python, OCR, Ollama, AI 모델)…"
LangString T_COMPOS ${LANG_ENGLISH} "Installing components (Python, OCR, Ollama, AI model)…"
LangString T_COMPOS ${LANG_FRENCH}  "Installation des composants (Python, OCR, Ollama, modèle IA)…"
LangString T_DESINST ${LANG_KOREAN}  "Ollama, AI 모델, Tesseract OCR은 다른 프로그램도 쓸 수 있어서 남겨 둡니다. 필요 없으면 Windows의 「앱」에서 따로 제거하세요."
LangString T_DESINST ${LANG_ENGLISH} "Ollama, the AI model and Tesseract OCR are kept, since other programs may use them. Remove them from Windows Apps if you no longer need them."
LangString T_DESINST ${LANG_FRENCH}  "Ollama, le modèle IA et Tesseract OCR sont conservés (d'autres programmes peuvent s'en servir). Supprime-les depuis « Applications » de Windows si besoin."
LangString T_CODE   ${LANG_KOREAN}  "ko"
LangString T_CODE   ${LANG_ENGLISH} "en"
LangString T_CODE   ${LANG_FRENCH}  "fr"

Var Vram
Var Radio4
Var Radio12
Var Radio0
Var Modele

Function .onInit
  !insertmacro MUI_LANGDLL_DISPLAY
  InitPluginsDir
  File "/oname=$PLUGINSDIR\gpu.ps1" "gpu.ps1"
  nsExec::ExecToStack 'powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$PLUGINSDIR\gpu.ps1"'
  Pop $1
  Pop $Vram
  ${If} $1 != 0
  ${OrIf} $Vram == ""
    StrCpy $Vram 0
  ${EndIf}
  StrCpy $Modele "translategemma:4b"
  ${If} $Vram >= 8
    StrCpy $Modele "translategemma:12b"
  ${EndIf}
FunctionEnd

Function PageModele
  !insertmacro MUI_HEADER_TEXT "$(T_TITRE)" "$(T_SOUS)"
  nsDialogs::Create 1018
  Pop $9
  StrCpy $0 $Vram
  ${If} $Vram == 0
    StrCpy $0 "?"
  ${EndIf}
  ${NSD_CreateLabel} 0 0 100% 14u "$(T_GPU)"
  ${NSD_CreateRadioButton} 0 22u 100% 14u "$(T_4B)"
  Pop $Radio4
  ${NSD_CreateRadioButton} 0 40u 100% 14u "$(T_12B)"
  Pop $Radio12
  ${NSD_CreateRadioButton} 0 58u 100% 14u "$(T_AUCUN)"
  Pop $Radio0
  ${NSD_CreateLabel} 0 84u 100% 30u "$(T_NOTE)"
  ${If} $Modele == "translategemma:12b"
    ${NSD_Check} $Radio12
  ${ElseIf} $Modele == "none"
    ${NSD_Check} $Radio0
  ${Else}
    ${NSD_Check} $Radio4
  ${EndIf}
  nsDialogs::Show
FunctionEnd

Function PageModeleQuitter
  ${NSD_GetState} $Radio12 $1
  ${NSD_GetState} $Radio0 $2
  ${If} $1 == ${BST_CHECKED}
    StrCpy $Modele "translategemma:12b"
  ${ElseIf} $2 == ${BST_CHECKED}
    StrCpy $Modele "none"
  ${Else}
    StrCpy $Modele "translategemma:4b"
  ${EndIf}
FunctionEnd

Function Lancer
  Exec '"$INSTDIR\python\pythonw.exe" "$INSTDIR\app\lanceur.py"'
FunctionEnd

Section "Book Translator"
  SetShellVarContext current
  ; arrête le programme s'il tourne (fichiers verrouillés)
  nsExec::Exec `powershell.exe -NoProfile -Command "Get-Process pythonw,python -ErrorAction SilentlyContinue | Where-Object { $$_.Path -like '$INSTDIR\*' } | Stop-Process -Force"`
  SetOutPath "$INSTDIR\app"
  File /r "build\app\*.*"
  SetOutPath "$INSTDIR"
  File "icon.ico"
  SetOutPath "$INSTDIR\setup"
  File "install.ps1"

  DetailPrint "$(T_COMPOS)"
  ClearErrors
  ExecWait 'powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$INSTDIR\setup\install.ps1" -InstallDir "$INSTDIR" -Lang $(T_CODE) -Model "$Modele"' $0
  ${If} ${Errors}
  ${OrIf} $0 != 0
    MessageBox MB_ICONSTOP "$(T_ECHEC)"
    Abort
  ${EndIf}

  SetOutPath "$INSTDIR\app"
  CreateShortCut "$DESKTOP\${APP}.lnk" "$INSTDIR\python\pythonw.exe" '"$INSTDIR\app\lanceur.py"' "$INSTDIR\icon.ico" 0
  CreateDirectory "$SMPROGRAMS\${APP}"
  CreateShortCut "$SMPROGRAMS\${APP}\${APP}.lnk" "$INSTDIR\python\pythonw.exe" '"$INSTDIR\app\lanceur.py"' "$INSTDIR\icon.ico" 0
  CreateShortCut "$SMPROGRAMS\${APP}\Uninstall.lnk" "$INSTDIR\uninstall.exe"

  WriteUninstaller "$INSTDIR\uninstall.exe"
  WriteRegStr HKCU "${UNINST_KEY}" "DisplayName" "${APP}"
  WriteRegStr HKCU "${UNINST_KEY}" "DisplayVersion" "${VERSION}"
  WriteRegStr HKCU "${UNINST_KEY}" "DisplayIcon" "$INSTDIR\icon.ico"
  WriteRegStr HKCU "${UNINST_KEY}" "UninstallString" '"$INSTDIR\uninstall.exe"'
  WriteRegStr HKCU "${UNINST_KEY}" "InstallLocation" "$INSTDIR"
  WriteRegDWORD HKCU "${UNINST_KEY}" "NoModify" 1
  WriteRegDWORD HKCU "${UNINST_KEY}" "NoRepair" 1
SectionEnd

Function un.onInit
  !insertmacro MUI_UNGETLANGUAGE
FunctionEnd

Section "Uninstall"
  SetShellVarContext current
  ; arrête le programme s'il tourne (fichiers verrouillés)
  nsExec::Exec `powershell.exe -NoProfile -Command "Get-Process pythonw,python -ErrorAction SilentlyContinue | Where-Object { $$_.Path -like '$INSTDIR\*' } | Stop-Process -Force"`
  SetOutPath "$TEMP"
  Delete "$DESKTOP\${APP}.lnk"
  RMDir /r "$SMPROGRAMS\${APP}"
  RMDir /r "$INSTDIR"
  DeleteRegKey HKCU "${UNINST_KEY}"
  DeleteRegKey HKCU "Software\BookTranslator"
  MessageBox MB_ICONINFORMATION "$(T_DESINST)"
SectionEnd
