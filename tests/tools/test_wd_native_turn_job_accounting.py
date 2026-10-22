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


def fake_source(text):
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
        'CloseHandle': 'return true;',
        'QueryInformationJobObject': '''
            if(job.ToInt32()!=101 || kind!=1 || size!=Marshal.SizeOf(typeof(ACCOUNTING)) || returned!=IntPtr.Zero)
                throw new Exception("query not bound to the exact owned job");
            info=new ACCOUNTING {total=FakeTotal,active=FakeActive};
            FakeQueries++; return !FakeQueryFailure;''',
    }

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
