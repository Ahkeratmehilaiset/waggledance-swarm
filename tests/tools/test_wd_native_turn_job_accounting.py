"""Actual type with every native declaration replaced before compilation."""
import json
import re
from pathlib import Path

import pytest

from test_wd_reboot_bundle import LANE_TEST_SHELLS, REBOOT, _run_powershell
from test_wd_startup_recovery import q


NAMES = {'CreateJobObject', 'SetInformationJobObject', 'QueryInformationJobObject',
         'QueryJobProcessIds', 'OpenProcess', 'IsProcessInJob', 'QueryFullProcessImageName',
         'AssignProcessToJobObject', 'TerminateJobObject', 'CreateFile', 'CreateProcess',
         'ResumeThread', 'WaitForSingleObject', 'GetExitCodeProcess', 'TerminateProcess', 'CloseHandle'}
DECLARATIONS = re.compile(r'\[DllImport\([^\]]*\)\]\s*(?P<declaration>static extern [^;]+;)', re.S)


def source():
    return (REBOOT / 'Invoke-WdLaneTurnLoop.ps1').read_text(encoding='utf-8').split("Add-Type -TypeDefinition @'\n", 1)[1].split("\n'@", 1)[0]


def fake_source(text, root_stop=False):
    declarations = list(DECLARATIONS.finditer(text))
    names = [re.search(r'(\w+)\s*\(', match['declaration']).group(1) for match in declarations]
    assert len(names) == 16 and set(names) == NAMES
    bodies = {
        'CreateJobObject': 'return new IntPtr(101);',
        'SetInformationJobObject': 'if(job.ToInt32()!=101 || kind!=9 || info.basic.flags!=0x2000) throw new Exception("job setup changed"); return true;',
        'CreateFile': 'return new IntPtr(400+(++FakeFiles));',
        'CreateProcess': 'if(!inherit || flags!=0x08000004) throw new Exception("suspended creation changed"); pi=new PROCESS_INFORMATION {process=new IntPtr(201),thread=new IntPtr(301),pid=4242,tid=4243}; return true;',
        'AssignProcessToJobObject': 'if(job.ToInt32()!=101 || process.ToInt32()!=201) throw new Exception("wrong owned job"); return true;',
        'ResumeThread': 'if(thread.ToInt32()!=301) throw new Exception("wrong thread"); return 1;',
        'CloseHandle': 'FakeCloses++; return true;',
        'QueryInformationJobObject': '''
            if(job.ToInt32()!=101 || kind!=1 || size!=Marshal.SizeOf(typeof(ACCOUNTING)) || returned!=IntPtr.Zero)
                throw new Exception("query not bound to the exact owned job");
            info=new ACCOUNTING {total=FakeTotal,active=FakeActive};
            FakeQueries++; return !FakeQueryFailure;''',
    }
    if root_stop:
        bodies['TerminateProcess'] = 'if(process.ToInt32()!=201 || code!=124) throw new Exception("not the owned root handle"); FakeRootCalls++; return !FakeRootFailure;'
        bodies['TerminateJobObject'] = 'if(job.ToInt32()!=101 || code!=124) throw new Exception("wrong outer safety job"); FakeJobCalls++; return true;'

    def replace(match):
        declaration = match['declaration']
        name = re.search(r'(\w+)\s*\(', declaration).group(1)
        body = bodies.get(name, 'throw new Exception("unexpected fake operation: '+name+'");')
        return declaration.replace('static extern ', 'static ', 1).rstrip(';')+' {'+body+'}'

    result = DECLARATIONS.sub(replace, text).replace('Marshal.GetLastWin32Error()', 'FakeLastError()')
    assert result.count('IntPtr job,process;') == 1
    result = result.replace('IntPtr job,process;', '''
        public static uint FakeTotal,FakeActive;
        public static bool FakeQueryFailure;
        public static int FakeFiles,FakeQueries;
        public static int FakeRootCalls,FakeJobCalls,FakeCloses;
        public static bool FakeRootFailure;
        static int FakeLastError() {return 5;}
        IntPtr job,process;''')
    assert not re.search(r'DllImport|\bextern\b|kernel32|NativeLibrary|GetLastWin32Error', result)
    return result


HARNESS = r'''
public static class AccountingHarness {
    public static object Run(uint total,uint active,bool failure) {
        WdManagedTurnProcess.FakeTotal=total;
        WdManagedTurnProcess.FakeActive=active;
        WdManagedTurnProcess.FakeQueryFailure=failure;
        using(var value=new WdManagedTurnProcess("synthetic",new string[0],"cwd","in","out","err")) {
            var property=typeof(WdManagedTurnProcess).GetProperty("TotalProcesses");
            if(property==null) throw new MissingMemberException("TotalProcesses absent");
            if(property.PropertyType!=typeof(uint) || property.GetSetMethod()!=null || property.GetGetMethod()==null)
                throw new Exception("TotalProcesses must be public uint read-only");
            try {return new {total=property.GetValue(value,null),active=value.ActiveProcesses,error=(string)null};}
            catch(System.Reflection.TargetInvocationException error) {
                var native=error.InnerException as System.ComponentModel.Win32Exception;
                if(native==null) throw;
                return new {error=native.GetType().Name,code=native.NativeErrorCode};
            }
        }
    }
}
'''


@pytest.mark.parametrize('ps', LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
@pytest.mark.parametrize('total,active,failure', [(17, 3, False), (2, 0, False), (3, 0, False), (1, 1, False), (4294967295, 0, False), (2, 0, True)])
def test_total_getter_uses_real_accounting_with_strict_fakes(ps, total, active, failure):
    code = fake_source(source()) + HARNESS
    assert not re.search(r'DllImport|\bextern\b|kernel32|NativeLibrary', code)
    script = 'Add-Type -TypeDefinition '+q(code)+'\n[AccountingHarness]::Run('+str(total)+','+str(active)+','+('$true' if failure else '$false')+')|ConvertTo-Json'
    completed = _run_powershell(script, executable=ps, check=False)
    assert completed.returncode == 0, completed.stderr
    value = json.loads(completed.stdout)
    assert value == ({'error': 'Win32Exception', 'code': 5} if failure else {'total': total, 'active': active, 'error': None})


@pytest.mark.parametrize('change', ['unknown_import', 'unmatched_extern'])
def test_fake_builder_refuses_before_compilation(change):
    text = source()
    text = (text.replace('static extern IntPtr CreateJobObject(', 'static extern IntPtr UnknownNative(')
            if change == 'unknown_import' else text+'\nclass Unsafe {static extern void UnknownNative();}')
    with pytest.raises(AssertionError):
        fake_source(text)


@pytest.mark.parametrize('ps', LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
@pytest.mark.parametrize('failure', [False, True])
def test_stop_root_only_keeps_outer_job_open_until_explicit_cleanup(ps, failure):
    harness = r'''
public static class RootOnlyHarness {
    public static object Run(bool failure) {
        var value=new WdManagedTurnProcess("synthetic",new string[0],"cwd","in","out","err");
        var method=typeof(WdManagedTurnProcess).GetMethod("StopRootOnly",Type.EmptyTypes);
        if(method==null) throw new MissingMemberException("StopRootOnly absent");
        if(method.ReturnType!=typeof(void)) throw new Exception("root-only API must return void/throw");
        int before=WdManagedTurnProcess.FakeCloses;
        WdManagedTurnProcess.FakeRootFailure=failure;
        string error=null;
        try {method.Invoke(value,null);}
        catch(System.Reflection.TargetInvocationException e) {
            var win=e.InnerException as System.ComponentModel.Win32Exception;
            if(win==null || win.NativeErrorCode!=5) throw;
            error="Win32Exception5";
        }
        if(WdManagedTurnProcess.FakeRootCalls!=1 || WdManagedTurnProcess.FakeJobCalls!=0 || WdManagedTurnProcess.FakeCloses!=before)
            throw new Exception("root-only stop closed/killed the outer safety job");
        value.Stop();
        if(WdManagedTurnProcess.FakeJobCalls!=1) throw new Exception("later outer stop missing");
        value.Dispose();
        if(WdManagedTurnProcess.FakeCloses!=before+2) throw new Exception("later disposal missing");
        return new {root_calls=1,job_calls=1,error=error};
    }
}
'''
    code = fake_source(source(), root_stop=True) + harness
    completed = _run_powershell('Add-Type -TypeDefinition '+q(code)+'\n[RootOnlyHarness]::Run('+('$true' if failure else '$false')+')|ConvertTo-Json', executable=ps, check=False)
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout) == {'root_calls': 1, 'job_calls': 1, 'error': 'Win32Exception5' if failure else None}
