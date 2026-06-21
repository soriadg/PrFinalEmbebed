from __future__ import annotations

import os
import sys
from textwrap import dedent

import pexpect


HOST = os.getenv("BBB_HOST", "beaglebone.local")
USER = os.getenv("BBB_USER", "debian")
PASSWORD = os.getenv("BBB_PASSWORD")


def run_command(child: pexpect.spawn, command: str, password: str, timeout: int = 30) -> None:
    child.sendline(command)
    index = child.expect(
        [
            r"\[sudo\] password for .*:",
            r"[#$] ",
            pexpect.TIMEOUT,
            pexpect.EOF,
        ],
        timeout=timeout,
    )
    if index == 0:
        child.sendline(password)
        followup = child.expect([r"[#$] ", pexpect.TIMEOUT, pexpect.EOF], timeout=timeout)
        if followup != 0:
            raise RuntimeError(f"Fallo ejecutando comando con sudo: {command}")
    elif index != 1:
        raise RuntimeError(f"Fallo ejecutando comando: {command}")


def main() -> int:
    if not PASSWORD:
        print("Falta BBB_PASSWORD en el entorno.", file=sys.stderr)
        return 1

    ssh_command = (
        f"ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null "
        f"-o ConnectTimeout=10 {USER}@{HOST}"
    )

    child = pexpect.spawn(ssh_command, encoding="utf-8", timeout=20)
    child.logfile = sys.stdout

    login_result = child.expect(
        [
            r"Are you sure you want to continue connecting \(yes/no(/\[fingerprint\])?\)\?",
            r"[Pp]assword:",
            r"[#$] ",
            pexpect.TIMEOUT,
            pexpect.EOF,
        ]
    )

    if login_result == 0:
        child.sendline("yes")
        login_result = child.expect([r"[Pp]assword:", r"[#$] ", pexpect.TIMEOUT, pexpect.EOF])

    if login_result == 1:
        child.sendline(PASSWORD)
        login_result = child.expect([r"[#$] ", r"[Pp]assword:", pexpect.TIMEOUT, pexpect.EOF], timeout=20)
        if login_result != 0:
            print("No se pudo iniciar sesion por SSH.", file=sys.stderr)
            return 1
    elif login_result != 2:
        print("No se pudo conectar por SSH.", file=sys.stderr)
        return 1

    remote_script = dedent(
        """\
        set -eu

        echo "== HOST =="
        hostname
        echo

        echo "== IP =="
        hostname -I || true
        ip -br addr || true
        echo

        echo "== RUTAS =="
        ip route || true
        echo

        echo "== ENLACES =="
        ip -br link || true
        echo

        if ! command -v mosquitto >/dev/null 2>&1; then
          echo "== INSTALANDO MOSQUITTO =="
          apt-get update
          DEBIAN_FRONTEND=noninteractive apt-get install -y mosquitto mosquitto-clients
          echo
        fi

        mkdir -p /etc/mosquitto/conf.d
        cat >/etc/mosquitto/conf.d/reed-monitor.conf <<'EOF'
        listener 1883
        allow_anonymous true
        persistence true
        EOF

        echo "== REINICIANDO SERVICIO =="
        if command -v systemctl >/dev/null 2>&1; then
          systemctl enable mosquitto
          systemctl restart mosquitto
          systemctl --no-pager --full status mosquitto | sed -n '1,20p'
        else
          pkill mosquitto || true
          /usr/sbin/mosquitto -c /etc/mosquitto/mosquitto.conf -d
        fi
        echo

        echo "== PUERTO 1883 =="
        ss -ltnp | grep ':1883' || netstat -ltnp | grep ':1883'
        echo

        echo "== PRUEBA LOCAL MQTT =="
        timeout 5 sh -c "mosquitto_sub -h 127.0.0.1 -t test/topic -C 1 >/tmp/mqtt_check.txt & \
        sleep 1; \
        mosquitto_pub -h 127.0.0.1 -t test/topic -m hola_desde_bbb; \
        wait"
        cat /tmp/mqtt_check.txt
        echo

        echo "== LISTO =="
        echo "Broker MQTT escuchando en 1883"
        """
    )

    commands = [
        "sudo -S bash -lc " + repr(remote_script),
    ]

    try:
        for command in commands:
            run_command(child, command, PASSWORD, timeout=60)
    except RuntimeError as error:
        print(str(error), file=sys.stderr)
        return 1
    finally:
        child.sendline("exit")
        child.expect(pexpect.EOF, timeout=10)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
