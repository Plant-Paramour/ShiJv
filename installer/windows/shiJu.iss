#ifndef AppVersion
  #define AppVersion "0.1.0"
#endif
#ifndef PayloadDir
  #define PayloadDir AddBackslash(SourcePath) + "..\..\dist\windows-installer-payload"
#endif
#ifndef OutputDir
  #define OutputDir AddBackslash(SourcePath) + "..\..\dist"
#endif

[Setup]
AppId={{E6D28CB5-CB7E-4C36-9E57-CE6D3F347CD4}
AppName=诗矩
AppVersion={#AppVersion}
AppPublisher=诗矩
AppPublisherURL=https://github.com/Plant-Paramour/ShiJv
DefaultDirName={localappdata}\Programs\ShiJu
DefaultGroupName=诗矩
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64
ArchitecturesInstallIn64BitMode=x64
UninstallDisplayName=诗矩
UninstallDisplayIcon={app}\诗矩单机版.exe
OutputDir={#OutputDir}
OutputBaseFilename=诗矩安装程序
SetupLogging=yes
WizardStyle=modern
Compression=lzma2/ultra64
SolidCompression=yes
ChangesEnvironment=no
CloseApplications=no
RestartApplications=no

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "快捷方式："; Flags: unchecked

[Files]
Source: "{#PayloadDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\诗矩"; Filename: "{app}\诗矩单机版.exe"; WorkingDir: "{app}"; Comment: "启动诗矩"
Name: "{autoprograms}\诗矩环境部署（维护）"; Filename: "{app}\诗矩环境部署.exe"; WorkingDir: "{app}"; Comment: "检查电脑并准备运行环境"
Name: "{autodesktop}\诗矩"; Filename: "{app}\诗矩单机版.exe"; WorkingDir: "{app}"; Tasks: desktopicon; Comment: "启动诗矩"

[Code]
var
  SourcePage, ModelPage: TWizardPage;
  RuntimeLocal, ModelLocal: TNewCheckBox;
  Model08, Model2, Model4: TNewRadioButton;
  RuntimePath, ModelPath: TNewEdit;
  RuntimeBrowse, ModelBrowse: TNewButton;
  DeployStarted: Boolean;
  RequestPath, EventsPath, ResultPath, DataPath: String;

procedure RuntimeLocalClick(Sender: TObject); forward;
procedure ModelLocalClick(Sender: TObject); forward;
procedure RuntimeBrowseClick(Sender: TObject); forward;
procedure ModelBrowseClick(Sender: TObject); forward;

function JsonEscape(Value: String): String;
begin
  StringChangeEx(Value, '\\', '\\\\', True);
  StringChangeEx(Value, '"', '\\"', True);
  Result := Value;
end;

procedure InitializeWizard;
begin
  SourcePage := CreateCustomPage(wpSelectDir, '选择运行环境和模型来源', '环境和模型可以分别选择本地已有资源或由安装器下载。');
  RuntimeLocal := TNewCheckBox.Create(SourcePage);
  RuntimeLocal.Parent := SourcePage.Surface;
  RuntimeLocal.Caption := '使用本地已有运行环境';
  RuntimeLocal.Left := 0; RuntimeLocal.Top := 12; RuntimeLocal.Width := 400;
  RuntimePath := TNewEdit.Create(SourcePage);
  RuntimePath.Parent := SourcePage.Surface;
  RuntimePath.Text := '';
  RuntimePath.Left := 0; RuntimePath.Top := 42; RuntimePath.Width := 430;
  RuntimePath.Enabled := False;
  RuntimeBrowse := TNewButton.Create(SourcePage);
  RuntimeBrowse.Parent := SourcePage.Surface;
  RuntimeBrowse.Caption := '浏览…'; RuntimeBrowse.Left := 440; RuntimeBrowse.Top := 40; RuntimeBrowse.Width := 80;
  RuntimeBrowse.Enabled := False; RuntimeBrowse.OnClick := @RuntimeBrowseClick;
  RuntimeLocal.OnClick := @RuntimeLocalClick;

  ModelPage := CreateCustomPage(SourcePage.ID, '选择 Qwen3.5 模型', '当前版本支持 Qwen3.5 0.8B、2B、4B。');
  ModelLocal := TNewCheckBox.Create(ModelPage);
  ModelLocal.Parent := ModelPage.Surface;
  ModelLocal.Caption := '使用本地已有模型';
  ModelLocal.Left := 0; ModelLocal.Top := 12; ModelLocal.Width := 400;
  ModelPath := TNewEdit.Create(ModelPage);
  ModelPath.Parent := ModelPage.Surface;
  ModelPath.Text := '';
  ModelPath.Left := 0; ModelPath.Top := 42; ModelPath.Width := 430;
  ModelPath.Enabled := False;
  ModelBrowse := TNewButton.Create(ModelPage);
  ModelBrowse.Parent := ModelPage.Surface;
  ModelBrowse.Caption := '浏览…'; ModelBrowse.Left := 440; ModelBrowse.Top := 40; ModelBrowse.Width := 80;
  ModelBrowse.Enabled := False; ModelBrowse.OnClick := @ModelBrowseClick;
  ModelLocal.OnClick := @ModelLocalClick;
  Model08 := TNewRadioButton.Create(ModelPage); Model08.Parent := ModelPage.Surface; Model08.Caption := 'Qwen3.5 0.8B（资源占用较低）'; Model08.Left := 0; Model08.Top := 90;
  Model2 := TNewRadioButton.Create(ModelPage); Model2.Parent := ModelPage.Surface; Model2.Caption := 'Qwen3.5 2B（默认）'; Model2.Left := 0; Model2.Top := 120; Model2.Checked := True;
  Model4 := TNewRadioButton.Create(ModelPage); Model4.Parent := ModelPage.Surface; Model4.Caption := 'Qwen3.5 4B（模型更大）'; Model4.Left := 0; Model4.Top := 150;
end;

procedure RuntimeLocalClick(Sender: TObject);
begin
  RuntimePath.Enabled := RuntimeLocal.Checked;
  RuntimeBrowse.Enabled := RuntimeLocal.Checked;
end;

procedure CurPageChanged(CurPageID: Integer);
begin
  if CurPageID = SourcePage.ID then begin
    if RuntimePath.Text = '' then RuntimePath.Text := AddBackslash(WizardDirValue) + 'environment';
  end;
  if CurPageID = ModelPage.ID then begin
    if ModelPath.Text = '' then ModelPath.Text := AddBackslash(WizardDirValue) + 'model';
  end;
end;

procedure ModelLocalClick(Sender: TObject);
begin
  ModelPath.Enabled := ModelLocal.Checked;
  ModelBrowse.Enabled := ModelLocal.Checked;
  Model08.Enabled := not ModelLocal.Checked;
  Model2.Enabled := not ModelLocal.Checked;
  Model4.Enabled := not ModelLocal.Checked;
end;

procedure RuntimeBrowseClick(Sender: TObject);
var Selected: String;
begin
  Selected := RuntimePath.Text;
  if BrowseForFolder('选择已有运行环境目录', Selected, False) then RuntimePath.Text := Selected;
end;

procedure ModelBrowseClick(Sender: TObject);
var Selected: String;
begin
  Selected := ModelPath.Text;
  if BrowseForFolder('选择已有模型目录', Selected, False) then ModelPath.Text := Selected;
end;

function SelectedModel: String;
begin
  if Model08.Checked then Result := 'Qwen/Qwen3.5-0.8B'
  else if Model4.Checked then Result := 'Qwen/Qwen3.5-4B'
  else Result := 'Qwen/Qwen3.5-2B';
end;

function NextButtonClick(CurPageID: Integer): Boolean;
var
  Json: String;
  RuntimeSource, ModelSource: String;
begin
  Result := True;
  if (CurPageID <> ModelPage.ID) or DeployStarted then Exit;
  if RuntimeLocal.Checked then RuntimeSource := 'local' else RuntimeSource := 'new';
  if ModelLocal.Checked then ModelSource := 'local' else ModelSource := 'official';
  if RuntimeLocal.Checked and (Trim(RuntimePath.Text) = '') then begin MsgBox('请选择本地运行环境目录。', mbError, MB_OK); Result := False; Exit; end;
  if ModelLocal.Checked and (Trim(ModelPath.Text) = '') then begin MsgBox('请选择本地模型目录。', mbError, MB_OK); Result := False; Exit; end;
  RequestPath := ExpandConstant('{tmp}\installer-request.json');
  DataPath := ExpandConstant('{localappdata}\ShiJu\deployment');
  Json := '{"runtime_source":"' + RuntimeSource + '","runtime_path":"' + JsonEscape(RuntimePath.Text) + '","model_source":"' + ModelSource + '","model_path":"' + JsonEscape(ModelPath.Text) + '","model_id":"' + SelectedModel + '","profile":"auto","action":"install"}';
  SaveStringToFile(RequestPath, Json, False);
  DeployStarted := False;
end;

procedure RunDeployment;
var ExitCode: Integer;
  FailureText: String;
begin
  if DeployStarted then Exit;
  DeployStarted := True;
  EventsPath := ExpandConstant('{app}\installer-events.jsonl');
  ResultPath := ExpandConstant('{app}\installer-result.json');
  WizardForm.StatusLabel.Caption := '正在准备 Python、PyTorch、模型并验证运行环境，请稍候……';
  if not Exec(ExpandConstant('{app}\ShiJuSetupBootstrap.exe'), '--request "' + RequestPath + '" --events "' + EventsPath + '" --result "' + ResultPath + '" --data-dir "' + DataPath + '"', '', SW_HIDE, ewWaitUntilTerminated, ExitCode) then begin
    MsgBox('无法启动安装部署引擎。' + #13#10 + '请查看安装目录中的诊断文件。', mbError, MB_OK); Exit; end;
  if (ExitCode <> 0) or not FileExists(ResultPath) then begin
    FailureText := '环境部署未完成。' + #13#10 + '退出码：' + IntToStr(ExitCode);
    FailureText := FailureText + #13#10 + '详细事件：' + EventsPath + #13#10 + '结果文件：' + ResultPath;
    MsgBox(FailureText, mbError, MB_OK); Exit; end;
  WizardForm.StatusLabel.Caption := '环境部署和真实验证已完成。';
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if (CurStep = ssPostInstall) and (RequestPath <> '') and FileExists(RequestPath) then RunDeployment;
end;
