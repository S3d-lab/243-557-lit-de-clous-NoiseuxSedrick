"""Scan I2C1 et I2C3 via USB, sans STM32Hardware.connect()."""
import argparse
import serial


def exchange(link, command):
    link.write((command + "\n").encode("ascii"))
    response = link.readline().decode("ascii", errors="replace").strip()
    if not response:
        raise RuntimeError(f"Aucune reponse a {command!r}. Verifie Run, USB et port COM.")
    return response


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", default="COM5")
    args = parser.parse_args()
    with serial.Serial(args.port, 115200, timeout=2, write_timeout=2) as link:
        link.reset_input_buffer()
        response = exchange(link, "PING")
        print("PING :", response, flush=True)
        if response != "PONG":
            raise RuntimeError("Reponse inattendue : verifie le port et le firmware.")
        for bus in (1, 3):
            print(f"\nScan I2C{bus} en cours...", flush=True)
            found = []
            for address in range(0x08, 0x78):
                response = exchange(link, f"I2C PROBE {bus} {address:03d}")
                if response == "ACK":
                    found.append(address)
                    print(f"  Adresse detectee : 0x{address:02X}", flush=True)
                elif response != "NACK":
                    raise RuntimeError(
                        f"I2C{bus}, adresse 0x{address:02X} : {response}. "
                        "Si ERROR, transfere le nouveau command_handler.c."
                    )
            addresses = " ".join(f"0x{a:02X}" for a in found)
            print(f"RESULTAT I2C{bus} : {addresses or 'aucune adresse detectee'}", flush=True)


if __name__ == "__main__":
    try:
        main()
    except (serial.SerialException, RuntimeError) as error:
        raise SystemExit(f"Erreur : {error}")
