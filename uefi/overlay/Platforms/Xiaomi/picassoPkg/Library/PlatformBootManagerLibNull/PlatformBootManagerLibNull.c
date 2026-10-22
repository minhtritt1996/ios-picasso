/** @file
  Minimal PlatformBootManagerLib for picasso bring-up.

  The full Mu implementation (setup secure boot, update ACPI tables) needs
  drivers this port does not ship yet and hangs before the console exists, so
  every hook is a no-op here. BDS then connects the consoles and shows the menu.
**/
#include <Library/PlatformBootManagerLib.h>
#include <Library/UefiBootManagerLib.h>

VOID
EFIAPI
PlatformBootManagerBeforeConsole (
  VOID
  )
{
}

VOID
EFIAPI
PlatformBootManagerAfterConsole (
  VOID
  )
{
}

VOID
EFIAPI
PlatformBootManagerWaitCallback (
  UINT16 TimeoutRemain
  )
{
}

VOID
EFIAPI
PlatformBootManagerBdsEntry (
  VOID
  )
{
}

VOID
EFIAPI
PlatformBootManagerProcessBootCompletion (
  IN EFI_BOOT_MANAGER_LOAD_OPTION  *BootOption
  )
{
}

VOID
EFIAPI
PlatformBootManagerOnDemandConInConnect (
  VOID
  )
{
}

VOID
EFIAPI
PlatformBootManagerUnableToBoot (
  VOID
  )
{
}

VOID
EFIAPI
PlatformBootManagerPriorityBoot (
  UINT16  **BootNext
  )
{
}
