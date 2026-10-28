# Run with -Install once to register and start the per-user logon task.
[CmdletBinding()]
param([switch]$Install)

$taskName = 'convrisk-dashboard-tunnel'

if ($Install) {
    $user = [Security.Principal.WindowsIdentity]::GetCurrent().Name
    $powershell = Join-Path $env:WINDIR 'System32\WindowsPowerShell\v1.0\powershell.exe'
    $action = New-ScheduledTaskAction -Execute $powershell -Argument (
        '-NoLogo -NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File "{0}"' -f $PSCommandPath
    )
    $trigger = New-ScheduledTaskTrigger -AtLogOn -User $user
    $principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
    $settings = New-ScheduledTaskSettingsSet -Hidden -StartWhenAvailable `
        -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
        -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew `
        -RestartInterval (New-TimeSpan -Minutes 1) -RestartCount 999
    Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger `
        -Principal $principal -Settings $settings -Description (
            'Keeps the convrisk dashboard SSH forwards (8501/8503) connected while Dan is logged on.'
        ) -Force -ErrorAction Stop | Out-Null
    Start-ScheduledTask -TaskName $taskName -ErrorAction Stop
    Write-Output "Registered and started $taskName for $user."
    return
}

$logDirectory = Join-Path $env:LOCALAPPDATA 'opencode-companion\convrisk-tunnel'
New-Item -ItemType Directory -Path $logDirectory -Force -ErrorAction Stop | Out-Null
$logPath = Join-Path $logDirectory 'tunnel.log'

function Write-TunnelLog([string]$Message) {
    # Keep the current log and one previous log, each about 256 KiB at most.
    if ((Test-Path -LiteralPath $logPath) -and
        (Get-Item -LiteralPath $logPath).Length -ge 256KB) {
        Move-Item -LiteralPath $logPath -Destination "$logPath.1" -Force
    }
    Add-Content -LiteralPath $logPath -Encoding UTF8 -Value (
        '{0} {1}' -f (Get-Date -Format o), $Message
    )
}

# @beacon[
#   id=opc@Start-ConvriskTunnel,
#   slice_labels=opc-basic,
#   kind=ast,
#   comment=Supervises both dashboard forwards with three-second retries and rotating logs; waits for occupied ports and owns its child SSH lifetime.,
# ]
function Start-ConvriskTunnel {
    <# Retry the dashboard tunnel; stopping this task also stops only its own SSH child. #>
    # Task Scheduler can end PowerShell without ending a native child. A private
    # kill-on-close job makes our child exit with us, without finding/killing PIDs.
    Add-Type -TypeDefinition @'
using System;
using System.ComponentModel;
using System.Runtime.InteropServices;
public static class ConvriskTunnelJob {
    [StructLayout(LayoutKind.Sequential)] struct BasicLimits {
        public long ProcessTime, JobTime;
        public uint Flags;
        public UIntPtr MinWorkingSet, MaxWorkingSet;
        public uint ActiveProcesses;
        public UIntPtr Affinity;
        public uint Priority, Scheduling;
    }
    [StructLayout(LayoutKind.Sequential)] struct IoCounters {
        public ulong ReadOps, WriteOps, OtherOps, ReadBytes, WriteBytes, OtherBytes;
    }
    [StructLayout(LayoutKind.Sequential)] struct ExtendedLimits {
        public BasicLimits Basic;
        public IoCounters Io;
        public UIntPtr ProcessMemory, JobMemory, PeakProcessMemory, PeakJobMemory;
    }
    [DllImport("kernel32.dll", SetLastError=true)]
    static extern IntPtr CreateJobObject(IntPtr attributes, string name);
    [DllImport("kernel32.dll", SetLastError=true)]
    static extern bool SetInformationJobObject(IntPtr job, int infoClass,
        ref ExtendedLimits limits, uint length);
    [DllImport("kernel32.dll", SetLastError=true)]
    static extern bool AssignProcessToJobObject(IntPtr job, IntPtr process);
    [DllImport("kernel32.dll")] static extern IntPtr GetCurrentProcess();
    static IntPtr job; // Held until this PowerShell process exits.
    public static void Attach() {
        job = CreateJobObject(IntPtr.Zero, null);
        if (job == IntPtr.Zero) throw new Win32Exception();
        var limits = new ExtendedLimits();
        limits.Basic.Flags = 0x2000; // JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if (!SetInformationJobObject(job, 9, ref limits,
                (uint)Marshal.SizeOf(typeof(ExtendedLimits))) ||
            !AssignProcessToJobObject(job, GetCurrentProcess()))
            throw new Win32Exception();
    }
}
'@ -ErrorAction Stop
    [ConvriskTunnelJob]::Attach()
    $ssh = Join-Path $env:WINDIR 'System32\OpenSSH\ssh.exe'
    $sshArguments = @(
        '-N',
        '-o', 'ServerAliveInterval=30',
        '-o', 'ServerAliveCountMax=3',
        '-o', 'ExitOnForwardFailure=yes',
        '-L', '0.0.0.0:8501:localhost:8501',
        '-L', '0.0.0.0:8503:localhost:8503',
        'lambda-a100'
    )
    Write-TunnelLog 'Supervisor started; retrying SSH three seconds after each exit.'
    $waiting = $false
    while ($true) {
        try {
            # Windows can allow duplicate wildcard SSH binds. Wait explicitly for
            # an existing local tunnel; Tailscale's own IP listeners do not count.
            $occupied = @([System.Net.NetworkInformation.IPGlobalProperties]::GetIPGlobalProperties().GetActiveTcpListeners() |
                Where-Object { $_.Port -in 8501,8503 -and
                    $_.Address.ToString() -in '0.0.0.0','127.0.0.1','::','::1' })
            if ($occupied.Count) {
                if (!$waiting) { Write-TunnelLog 'Local dashboard ports occupied; waiting without touching their owner.' }
                $waiting = $true
                Start-Sleep -Seconds 3
                continue
            }
            $waiting = $false
            Write-TunnelLog 'Starting SSH forwards on 8501 and 8503.'
            & $ssh @sshArguments 2>&1 | ForEach-Object { Write-TunnelLog $_.ToString() }
            Write-TunnelLog "SSH exited with code $LASTEXITCODE; retrying in three seconds."
        }
        catch {
            Write-TunnelLog "SSH launch failed: $_; retrying in three seconds."
        }
        Start-Sleep -Seconds 3
    }
}

Start-ConvriskTunnel
