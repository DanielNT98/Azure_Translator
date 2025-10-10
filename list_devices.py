import sounddevice as sd

print("\n" + "=" * 80)
print("AVAILABLE AUDIO DEVICES")
print("=" * 80)

# Get default devices
default_in, default_out = None, None
try:
    d = sd.default.device
    if isinstance(d, (list, tuple)) and len(d) == 2:
        default_in, default_out = d[0], d[1]
except:
    pass

print(f"\nDefault INPUT device: {default_in}")
print(f"Default OUTPUT device: {default_out}\n")

print("=" * 80)
print("ALL DEVICES:")
print("=" * 80)

for i, dev in enumerate(sd.query_devices()):
    in_ch = dev.get("max_input_channels", 0)
    out_ch = dev.get("max_output_channels", 0)
    name = dev.get("name", "")
    
    default_marker = ""
    if i == default_in:
        default_marker += " [DEFAULT INPUT]"
    if i == default_out:
        default_marker += " [DEFAULT OUTPUT]"
    
    typ = []
    if in_ch > 0:
        typ.append(f"INPUT({in_ch}ch)")
    if out_ch > 0:
        typ.append(f"OUTPUT({out_ch}ch)")
    
    print(f"\n[{i:2d}] {' | '.join(typ):20s} {name}{default_marker}")

print("\n" + "=" * 80)
print("INPUT DEVICES (for microphone):")
print("=" * 80)

input_devices = []
for i, dev in enumerate(sd.query_devices()):
    if dev.get("max_input_channels", 0) > 0:
        name = dev.get("name", "")
        input_devices.append((i, name))
        default_marker = " [DEFAULT]" if i == default_in else ""
        print(f"  [{i:2d}] {name}{default_marker}")

print("\n" + "=" * 80)
print("RECOMMENDED .env CONFIGURATION:")
print("=" * 80)

if input_devices:
    print("\nChoose ONE of these for your .env file:\n")
    for idx, name in input_devices[:5]:
        default_note = " # <-- System default" if idx == default_in else ""
        print(f'  MIC_DEVICE_NAME="{name}"{default_note}')
else:
    print("\n⚠️  No input devices found!")

print("\n" + "=" * 80)
