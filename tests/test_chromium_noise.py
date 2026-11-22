import io
import os
import subprocess
import sys

from line import chromium_noise

_HDR_QUERY = (b"[123:456:1121/174001.151:ERROR:C:\\Users\\qt\\work\\qt\\qtwebengine_build\\src\\core\\RelWithDebInfo"
              b"\\AMD64\\../../../../../qtwebengine/src/3rdparty/chromium/ui/gl/direct_composition_support.cc:513] "
              b"QueryVideoProcessorCustomExtForHDR: Failed to retrieve D3D11 device\r\n")
_SOMETHING_ELSE = b"[123:456:1121/174001.152:ERROR:made_up.cc:1] something went wrong\r\n"


def test_chromiums_hdr_query_on_a_display_coming_or_going_is_left_out_and_every_other_line_passed_on():
    passed_on = io.BytesIO()

    chromium_noise.pass_on(io.BytesIO(_HDR_QUERY + _SOMETHING_ELSE + b"a line of Qt's own\n"), passed_on)

    assert passed_on.getvalue() == _SOMETHING_ELSE + b"a line of Qt's own\n"


def test_the_lines_left_out_include_those_a_child_program_writes_as_chromiums_gpu_process_does(capfd):
    with chromium_noise.left_out_of_stderr():
        subprocess.run([sys.executable, "-c", "import sys; sys.stderr.buffer.write(bytes.fromhex(sys.argv[1]))",
                        (_HDR_QUERY + _SOMETHING_ELSE).hex()], check=True)
        os.write(2, b"its own line\n")
    os.write(2, b"after it closed\n")

    assert capfd.readouterr().err.splitlines() == [_SOMETHING_ELSE.decode().rstrip(), "its own line", "after it closed"]


_A_PROGRAM_STARTED_WITH_NO_STDERR = """
import os
import sys
from line import chromium_noise
os.close(2)
with chromium_noise.left_out_of_stderr():
    print("ran", flush=True)
"""


def test_line_started_with_no_stderr_runs_as_before():
    ran = subprocess.run([sys.executable, "-c", _A_PROGRAM_STARTED_WITH_NO_STDERR], check=True, capture_output=True)

    assert ran.stdout.splitlines() == [b"ran"]
