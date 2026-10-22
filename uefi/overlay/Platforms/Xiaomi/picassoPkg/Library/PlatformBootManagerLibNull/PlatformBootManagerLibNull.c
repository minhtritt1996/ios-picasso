/** @file
  Minimal PlatformBootManagerLib for picasso bring-up.

  The full Mu implementation (setup secure boot, update ACPI tables) needs
  drivers this port does not ship yet and hangs before the console exists, so
  every hook is a no-op here. BDS then connects the consoles and shows the menu.
**/
#include <Library/PlatformBootManagerLib.h>
#include <Library/UefiBootManagerLib.h>

STATIC
VOID
PicassoBand (UINTN Row, UINT32 Colour)
{
  volatile UINT32 *Fb = (volatile UINT32 *)0xA0000000UL;
  UINTN            I;

  for (I = 0; I < 1080 * 20; I++) {
    Fb[(Row * 1080) + I] = Colour;
  }
}

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

/*
 * Nothing bootable: show the UEFI boot manager menu (BootManagerMenuApp is in
 * the FV and its FILE_GUID is PcdBootManagerMenuFile).
 */
VOID
EFIAPI
PlatformBootManagerUnableToBoot (
  VOID
  )
{
  EFI_BOOT_MANAGER_LOAD_OPTION Menu;
  EFI_STATUS                  Status;

  PicassoBand (950, 0x00FF8000);   /* UnableToBoot reached */
  Status = EfiBootManagerGetBootManagerMenu (&Menu);
  PicassoBand (980, EFI_ERROR (Status) ? 0x00FF0000 : 0x0000FFFF);
  if (EFI_ERROR (Status)) {
    return;
  }
  EfiBootManagerBoot (&Menu);
}

VOID
EFIAPI
PlatformBootManagerPriorityBoot (
  UINT16  **BootNext
  )
{
}
