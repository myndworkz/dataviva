from sqlalchemy import func, distinct, asc, desc, and_
from flask import Blueprint, request, jsonify, abort, g, render_template, make_response

from dataviva import db, __latest_year__
from dataviva.attrs.models import Bra, Wld, Hs, Isic, Cbo, Yb
from dataviva.secex.models import Yp, Yw
from dataviva.rais.models import Yi, Yo
from dataviva.utils import exist_or_404, gzip_data, cached_query, title_case

mod = Blueprint('attrs', __name__, url_prefix='/attrs')

@mod.errorhandler(404)
def page_not_found(error):
    return error, 404

def fix_name(attr, lang):
    name_lang = "name_" + lang
    desc_lang = "desc_" + lang
    keywords_lang = "keywords_" + lang
    if desc_lang in attr:
        attr["desc"] = title_case(attr[desc_lang])
        if "desc_en" in attr: del attr["desc_en"]
        if "desc_pt" in attr: del attr["desc_pt"]
    if name_lang in attr:
        attr["name"] = title_case(attr[name_lang])
        if "name_en" in attr: del attr["name_en"]
        if "name_pt" in attr: del attr["name_pt"]
    if keywords_lang in attr:
        attr["keywords"] = title_case(attr[keywords_lang])
        if "keywords_en" in attr: del attr["keywords_en"]
        if "keywords_pt" in attr: del attr["keywords_pt"]
    return attr

############################################################
# ----------------------------------------------------------
# All attribute views
# 
############################################################

@mod.route('/<attr>/')
@mod.route('/<attr>/<Attr_id>/')
def attrs(attr="bra",Attr_id=None):
    
    Attr = globals()[attr.title()]
    Attr_weight_mergeid = "{0}_id".format(attr)
    
    if attr == "bra":
        Attr_weight_tbl = Yb
        Attr_weight_col = "population"
    elif attr == "isic":
        Attr_weight_tbl = Yi
        Attr_weight_col = "num_emp"
    elif attr == "cbo":
        Attr_weight_tbl = Yo
        Attr_weight_col = "num_emp"
    elif attr == "hs":
        Attr_weight_tbl = Yp
        Attr_weight_col = "val_usd"
    elif attr == "wld":
        Attr_weight_tbl = Yw
        Attr_weight_col = "val_usd"
        
    depths = {}
    depths["bra"] = [2,4,7,8]
    depths["isic"] = [1,3,5]
    depths["cbo"] = [1,2,4]
    depths["hs"] = [2,4,6]
    depths["wld"] = [2,5]
    
    depth = request.args.get('depth', None)
    order = request.args.get('order', None)
    offset = request.args.get('offset', None)
    limit = request.args.get('limit', None)
    if offset:
        offset = float(offset)
        limit = limit or 50
        
    lang = request.args.get('lang', None) or g.locale
    ret = {}
    dataset = "rais"
    if Attr == Cbo or Attr == Hs:
        dataset = "secex"
    latest_year = __latest_year__[dataset]
        
    cache_id = request.path + lang
    if depth:
        cache_id = cache_id + "/" + depth
    # first lets test if this query is cached
    cached_q = cached_query(cache_id)
    if cached_q and limit is None:
        ret = make_response(cached_q)
        ret.headers['Content-Encoding'] = 'gzip'
        ret.headers['Content-Length'] = str(len(ret.data))
        return ret
    
    # if an ID is supplied only return that
    if Attr_id:
        
        # the '.show.' indicates that we are looking for a specific nesting
        if ".show." in Attr_id:
            this_attr, ret["nesting_level"] = Attr_id.split(".show.")
            # filter table by requested nesting level
            attrs = Attr.query \
                    .filter(Attr.id.startswith(this_attr)) \
                    .filter(func.char_length(Attr.id) == ret["nesting_level"]).all()

        # the 'show.' indicates that we are looking for a specific nesting
        elif "show." in Attr_id:
            ret["nesting_level"] = Attr_id.split(".")[1]
            # filter table by requested nesting level
            attrs = Attr.query.filter(func.char_length(Attr.id) == ret["nesting_level"]).all()
        
        # the '.' here means we want to see all attrs within a certain distance
        elif "." in Attr_id:
            this_attr, distance = Attr_id.split(".")
            this_attr = Attr.query.get_or_404(this_attr)
            attrs = this_attr.get_neighbors(distance)
        
        else:
            attrs = [Attr.query.get_or_404(Attr_id)]
        
        ret["data"] = [fix_name(a.serialize(), lang) for a in attrs]
    # an ID/filter was not provided
    else:
        query = db.session.query(Attr,Attr_weight_tbl) \
            .outerjoin(Attr_weight_tbl, and_(getattr(Attr_weight_tbl,"{0}_id".format(attr)) == Attr.id, Attr_weight_tbl.year == latest_year))
        if depth:
            query = query.filter(func.char_length(Attr.id) == depth)
        else:
            query = query.filter(func.char_length(Attr.id).in_(depths[attr]))
            
        if order:
            direction = "asc"
        
            if "." in order:
                o, direction = order.split(".")
            else:
                o = order
                
            if o == "name":
                o = "name_{0}".format(lang)
                
            if o == Attr_weight_col:
                order_table  = Attr_weight_tbl
            else:
                order_table = Attr
                
            if direction == "asc":
                query = query.order_by(asc(getattr(order_table,o)))
            elif direction == "desc":
                query = query.order_by(desc(getattr(order_table,o)))
                
        if limit:
            query = query.limit(limit).offset(offset)
        
        attrs_all = query.all()
        
        # just get items available in DB
        attrs_w_data = None
        if depth is None and limit is None:
            attrs_w_data = db.session.query(Attr, Attr_weight_tbl) \
                .filter(getattr(Attr_weight_tbl, Attr_weight_mergeid) == Attr.id) \
                .group_by(Attr.id)
            # raise Exception(attrs_w_data.all())
            attrs_w_data = [a[0].id for a in attrs_w_data]
                        
        attrs = []
        for i, a in enumerate(attrs_all):
            b = a[0].serialize()
            if a[1]:
                b[Attr_weight_col] = a[1].serialize()[Attr_weight_col]
            else:
                b[Attr_weight_col] = 0
            a = b
            if attrs_w_data:
                a["available"] = False
                if a["id"] in attrs_w_data:
                    a["available"] = True
            if Attr_weight_col == "population" and len(a["id"]) == 8 and a["id"][:2] == "mg":
                plr = Bra.query.get_or_404(a["id"]).pr2.first()
                if plr: a["plr"] = plr.id
            if order:
                a["rank"] = int(i+offset+1)
            attrs.append(fix_name(a, lang))
        
        ret["data"] = attrs
    
    ret = jsonify(ret)
    ret.data = gzip_data(ret.data)
    
    if limit is None and cached_q is None:
        cached_query(cache_id, ret.data)
            
    ret.headers['Content-Encoding'] = 'gzip'
    ret.headers['Content-Length'] = str(len(ret.data))
        
    return ret
    
@mod.route('/table/<attr>/<depth>/')
def attrs_table(attr="bra",depth="2"):
    g.page_type = "attrs"
    data_url = "/attrs/{0}/?depth={1}".format(attr,depth)
    return render_template("general/table.html", data_url=data_url)