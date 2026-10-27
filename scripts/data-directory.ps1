function Get-ShiJuDataDirectory([string]$Workspace) {
    $selection = Join-Path $Workspace 'var\local-data.json'
    if (Test-Path -LiteralPath $selection) {
        $record = Get-Content -LiteralPath $selection -Raw -Encoding UTF8 | ConvertFrom-Json
        $sameMachine = (-not $record.machine -or $record.machine -ieq $env:COMPUTERNAME)
        $sameUser = (-not $record.user_profile -or $record.user_profile -ieq $env:USERPROFILE)
        if ($sameMachine -and $sameUser) {
            $workspaceData = [IO.Path]::GetFullPath((Join-Path $Workspace 'var'))
            $chosen = [IO.Path]::GetFullPath((Join-Path $workspaceData $record.directory))
            if (-not $chosen.StartsWith($workspaceData + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) { throw '工作区数据副本必须位于 var 目录内' }
            return $chosen
        }
    }
    return Join-Path $env:LOCALAPPDATA 'ShiJu'
}
