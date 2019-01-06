'''
*********************************************************
Copyright @ 2019 EMC Corporation All Rights Reserved
pci topology tree module.
*********************************************************
'''
# -*- coding: utf-8 -*-


from infrasim.model.core.element import CElement

from infrasim import ArgsNotCorrect


class CPCITreeElement(CElement):
    def __init__(self, info):
        super(CPCITreeElement, self).__init__()
        self._info = info
        self._bus = None
        self._pri_bus = None
        self._addr = None
        self._sec_bus = self._info.get('sec_bus', None)
        self._device = self._info.get('device')
        self._chassis = None
        self._slot = None
        self._multifunction = None
        self._id = self._info.get('id')
        self._extra_args = {}
        # flag indicating whether it is in new domain because fw_cfg doesn't support new domain.
        self._in_domain = False

    def precheck(self):
        pass

    def init(self):
        self._addr = self._info.get('addr', '0.0')
        self._chassis = self._info.get('chassis', None)
        self._slot = self._info.get('slot', None)
        self._multifunction = self._info.get('multifunction', None)

    def handle_parms(self):
        args = {}
        args["id"] = self._id
        args["bus"] = self._bus
        if self._multifunction:
            args["multifunction"] = self._multifunction
        if self._addr:
            args["addr"] = self._addr
        if self._chassis:
            args['chassis'] = self._chassis
        if self._slot:
            args['slot'] = self._slot

        # merge args of concrete devices
        args.update(self._extra_args)

        opt_list = []
        opt_list.append("-device {}".format(self._device))
        for k, v in args.items():
            opt_list.append("{}={}".format(k, v))

        self.add_option(",".join(opt_list))

    def set_domain(self, in_domain):
        self._in_domain = in_domain

    def get_domain(self):
        return self._in_domain

    def assign_bus(self, bus_id, pri_bus, sec_bus):
        self._bus = bus_id
        self._pri_bus = pri_bus
        if self._sec_bus is None:
            self._sec_bus = sec_bus
        return self._sec_bus + 1

    def get_fw_cfg_info(self):
        # get fw cfg information for bios.
        ret = {}
        if self._in_domain is False:
            device, func = self._addr.split('.')
            ret['id'] = self._id
            ret['bdf'] = (self._pri_bus << 8) + (int(device, 16) << 3) + int(func)
            ret['sec_bus'] = self._sec_bus
        return ret


class CPCIRootPort(CPCITreeElement):
    def __init__(self, info):
        super(CPCIRootPort, self).__init__(info)


class CPCIUpStream(CPCITreeElement):
    def __init__(self, info):
        super(CPCIUpStream, self).__init__(info)


class CPCIDownStream(CPCITreeElement):
    def __init__(self, info):
        super(CPCIDownStream, self).__init__(info)


class CPCIVMD(CPCITreeElement):
    def __init__(self, info):
        super(CPCIVMD, self).__init__(info)
        self.__bar1_size = None
        self.__bar2_size = None
        self._chassis = None
        self._slot = None
        self._multifunction = 'on'
        self._addr = '5.5'
        self._in_domain = True

    def init(self):
        self.__bar1_size = self._info.get("bar1_size")
        self.__bar2_size = self._info.get("bar2_size")

    def handle_parms(self):
        if self.__bar1_size:
            self._extra_args["mbar1_size"] = self.__bar1_size
        if self.__bar2_size:
            self._extra_args["mbar2_size"] = self.__bar2_size

        super(CPCIVMD, self).handle_parms()

    def assign_bus(self, bus_id, _, sec_bus):
        # vmd device doesn't need pri_bus and sec_bus
        self._bus = bus_id
        return sec_bus

    def set_domain(self, in_domain):
        pass


class CPCITree(CElement):
    class_list = {"vmd": CPCIVMD,
                  "ioh3420": CPCIRootPort,
                  "x3130-upstream": CPCIUpStream,
                  "xio3130-downstream": CPCIDownStream
                  }

    def __init__(self, pci_tree_info):
        super(CPCITree, self).__init__()
        self.__pci_tree_info = pci_tree_info
        self.__sec_bus = 0
        self.__fw_cfg_obj = None
        # stores all sub devices includeing root port, upstream and downstream.
        self.__component_list = []

    def set_fw_cfg_obj(self, fw_cfg_obj):
        self.__fw_cfg_obj = fw_cfg_obj

    def precheck(self):
        if self.__pci_tree_info is None:
            raise ArgsNotCorrect("pci topology is required.")
        # check duplication of id or slot.

        def __get_all_id_and_slot(sub_devices):
            id_list = []
            slot_list = []

            for component in sub_devices:
                id_list.append(component['id'])
                if 'slot' in component:
                    slot_list.append(component['slot'])
                sub_ids, sub_slots = __get_all_id_and_slot(component.get('sub_devices', []))
                id_list.extend(sub_ids)
                slot_list.extend(sub_slots)
            return id_list, slot_list

        all_ids = []
        all_slots = []
        for rootbus in self.__pci_tree_info:
            sub_ids, sub_slots = __get_all_id_and_slot(rootbus.get('sub_devices', []))
            all_ids.extend(sub_ids)
            all_slots.extend(sub_slots)

        re_list = [x for x in sub_ids if sub_ids.count(x) > 1]
        if len(re_list) != 0:
            raise ArgsNotCorrect("PCIE device id:{} duplicated".format(set(re_list)))

        re_list = [x for x in all_slots if all_slots.count(x) > 1]
        if len(re_list) != 0:
            raise ArgsNotCorrect("PCIE device slot:{} duplicated".format(set(re_list)))

    def __build_tree(self, bus_id, pri_bus, in_new_domain, info_elements):
        # build device tree
        for info in info_elements:
            # create object by device type.
            class_type = CPCITree.class_list[info["device"]]
            obj = class_type(info)
            # assign bus, pribus, sec_bus
            self.__sec_bus = obj.assign_bus(bus_id, pri_bus, self.__sec_bus)
            obj.set_domain(in_new_domain)
            self.__component_list.append(obj)
            # iterate all sub devices behind.
            self.__build_tree(info["id"], self.__sec_bus, in_new_domain or obj.get_domain(),
                              info.get("sub_devices", []))

    def init(self):
        self.logger.info("pci tree start ")

        # currently, there is 1 root bus.
        self.__sec_bus = 1
        for rootbus in self.__pci_tree_info:
            self.__build_tree(rootbus["id"], 0, False, rootbus["sub_devices"])

        for pcie_obj in self.__component_list:
            pcie_obj.precheck()
            pcie_obj.init()
            cfg = pcie_obj.get_fw_cfg_info()
            self.logger.info(cfg)
            if self.__fw_cfg_obj and cfg:
                self.__fw_cfg_obj.add_topo(cfg)

        self.logger.info("topology end")

    def handle_parms(self):
        for element in self.__component_list:
            element.handle_parms()
            self.add_option(element.get_option())
