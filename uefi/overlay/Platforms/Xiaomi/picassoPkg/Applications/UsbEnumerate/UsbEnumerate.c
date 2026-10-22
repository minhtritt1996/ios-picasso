/** @file
  Minimal USB function client for picasso: enumerate with a fastboot-style
  device and then feed the bulk endpoints. It exists to answer one question
  on hardware: does the DWC3 DMA work in a UEFI environment (the SMMU is
  locked for the kernel, and the first transfer is where that shows).
**/
#include <Uefi.h>
#include <Library/BaseLib.h>
#include <Library/BaseMemoryLib.h>
#include <Library/UefiBootServicesTableLib.h>
#include <Library/UefiLib.h>
#include <Protocol/UsbFunctionIo.h>

STATIC EFI_USB_DEVICE_DESCRIPTOR mDeviceDescriptor = {
  .Length            = 18,
  .DescriptorType    = USB_DESC_TYPE_DEVICE,
  .BcdUSB            = 0x0200,
  .DeviceClass       = 0xFF,
  .DeviceSubClass    = 0x42,
  .DeviceProtocol    = 0x03,
  .MaxPacketSize0    = 64,
  .IdVendor          = 0x1209,
  .IdProduct         = 0x0001,
  .BcdDevice         = 0x0100,
  .StrManufacturer   = 0,
  .StrProduct        = 0,
  .StrSerialNumber   = 0,
  .NumConfigurations = 1,
};

STATIC EFI_USB_ENDPOINT_DESCRIPTOR mEpOut = {
  .Length          = 7,
  .DescriptorType  = USB_DESC_TYPE_ENDPOINT,
  .EndpointAddress = 0x01,
  .Attributes      = 0x02,
  .MaxPacketSize   = 512,
  .Interval        = 0,
};

STATIC EFI_USB_ENDPOINT_DESCRIPTOR mEpIn = {
  .Length          = 7,
  .DescriptorType  = USB_DESC_TYPE_ENDPOINT,
  .EndpointAddress = 0x81,
  .Attributes      = 0x02,
  .MaxPacketSize   = 512,
  .Interval        = 0,
};

STATIC EFI_USB_ENDPOINT_DESCRIPTOR *mEpTable[] = { &mEpOut, &mEpIn, NULL };

STATIC EFI_USB_INTERFACE_DESCRIPTOR mInterface = {
  .Length            = 9,
  .DescriptorType    = USB_DESC_TYPE_INTERFACE,
  .InterfaceNumber   = 0,
  .AlternateSetting  = 0,
  .NumEndpoints      = 2,
  .InterfaceClass    = 0xFF,
  .InterfaceSubClass = 0x42,
  .InterfaceProtocol = 0x03,
  .Interface         = 0,
};

STATIC EFI_USB_INTERFACE_INFO mInterfaceInfo = { &mInterface, mEpTable };
STATIC EFI_USB_INTERFACE_INFO *mInterfaceTable[] = { &mInterfaceInfo, NULL };

STATIC EFI_USB_CONFIG_DESCRIPTOR mConfig = {
  .Length             = 9,
  .DescriptorType     = USB_DESC_TYPE_CONFIG,
  .TotalLength        = 9 + 9 + 7 + 7,
  .NumInterfaces      = 1,
  .ConfigurationValue = 1,
  .Configuration      = 0,
  .Attributes         = 0x80,
  .MaxPower           = 0x32,
};

STATIC EFI_USB_CONFIG_INFO mConfigInfo = { &mConfig, mInterfaceTable };
STATIC EFI_USB_CONFIG_INFO *mConfigTable[] = { &mConfigInfo, NULL };
STATIC EFI_USB_DEVICE_INFO mDeviceInfo = { &mDeviceDescriptor, mConfigTable };

/* picasso bring-up marker: rows on the framebuffer (no UART on this board). */
STATIC
VOID
Band (UINTN Row, UINT32 Colour)
{
  volatile UINT32 *Fb = (volatile UINT32 *)0xA0000000UL;
  UINTN            I;

  for (I = 0; I < 1080 * 20; I++) {
    Fb[(Row * 1080) + I] = Colour;
  }
}

STATIC
EFI_STATUS
HandleSetup (
  IN EFI_USBFN_IO_PROTOCOL      *UsbFn,
  IN EFI_USB_DEVICE_REQUEST     *Request
  )
{
  UINTN  Len = 0;
  VOID  *Buf = NULL;

  if ((Request->RequestType & 0x60) == USB_REQ_TYPE_STANDARD &&
      Request->Request == USB_REQ_GET_DESCRIPTOR) {
    switch (Request->Value >> 8) {
      case USB_DESC_TYPE_DEVICE:
        Buf = &mDeviceDescriptor;
        Len = sizeof (mDeviceDescriptor);
        break;
      case USB_DESC_TYPE_CONFIG:
        Buf = &mConfig;
        Len = sizeof (mConfig);
        break;
      default:
        break;
    }
  }

  if (Buf == NULL) {
    /* Unsupported: stall is not wired here, keep the host retrying. */
    return EFI_UNSUPPORTED;
  }

  if (Len > Request->Length) {
    Len = Request->Length;
  }
  Band (1120, 0x0000FFFF);   /* control transfer being answered */
  return UsbFn->Transfer (UsbFn, 0, EfiUsbEndpointDirectionDeviceTx, &Len, Buf);
}

EFI_STATUS
EFIAPI
UsbEnumerateEntry (
  IN EFI_HANDLE        ImageHandle,
  IN EFI_SYSTEM_TABLE  *SystemTable
  )
{
  EFI_USBFN_IO_PROTOCOL     *UsbFn = NULL;
  EFI_STATUS                 Status;
  EFI_USBFN_MESSAGE          Message;
  EFI_USBFN_MESSAGE_PAYLOAD  Payload;
  UINTN                      PayloadSize;

  Status = gBS->LocateProtocol (&gEfiUsbFunctionIoProtocolGuid, NULL, (VOID **)&UsbFn);
  if (EFI_ERROR (Status)) {
    Band (1100, 0x00FF0000);   /* no UsbFnIo protocol */
    return Status;
  }
  Band (1100, 0x0000FF00);     /* protocol found */

  Status = UsbFn->StartController (UsbFn);
  Band (1140, EFI_ERROR (Status) ? 0x00FF0000 : 0x0000FFFF);

  if (EFI_ERROR (Status)) {
    return Status;
  }

  Status = UsbFn->ConfigureEnableEndpoints (UsbFn, &mDeviceInfo);
  Band (1180, EFI_ERROR (Status) ? 0x00FF0000 : 0x00FF00FF);

  for (;;) {
    PayloadSize = sizeof (Payload);
    Status      = UsbFn->EventHandler (UsbFn, &Message, &PayloadSize, &Payload);
    if (EFI_ERROR (Status)) {
      continue;
    }

    if (Message == EfiUsbMsgSetupPacket) {
      HandleSetup (UsbFn, &Payload.udr);
    }
  }
}
